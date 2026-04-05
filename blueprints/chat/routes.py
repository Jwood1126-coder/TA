"""Flask routes for the AI chat feature."""

import json
import time
import base64
from flask import (Blueprint, render_template, jsonify, request, Response,
                   current_app)
from models import db, ChatMessage

from .prompt import SYSTEM_PROMPT
from .tools import TOOLS, SERVER_TOOLS
from .executor import execute_tool
from .context import build_context

chat_bp = Blueprint('chat', __name__)

MAX_TOOL_ROUNDS = 3


def _api_call_with_retry(client, **kwargs):
    """Make an Anthropic API call with one retry on transient errors."""
    import anthropic
    transient = (
        anthropic.APIConnectionError,
        anthropic.APITimeoutError,
        anthropic.RateLimitError,
    )
    try:
        return client.messages.create(**kwargs)
    except transient:
        time.sleep(2)
        return client.messages.create(**kwargs)
    except anthropic.APIStatusError as e:
        if e.status_code == 529:
            time.sleep(2)
            return client.messages.create(**kwargs)
        raise


@chat_bp.route('/chat')
def chat_view():
    return render_template('chat.html')


@chat_bp.route('/api/chat/send', methods=['POST'])
def send_message():
    api_key = current_app.config.get('ANTHROPIC_API_KEY', '')
    if not api_key or api_key == 'sk-ant-your-key-here':
        return jsonify({'error': 'API key not configured. '
                        'Set ANTHROPIC_API_KEY in .env'}), 400

    # Handle both JSON and multipart form data
    images = []  # list of {data, media_type, filename}
    image_filename = None  # first image filename for DB record
    session_history_raw = []

    MODEL_MAP = {
        'fast': 'claude-haiku-4-5-20251001',
        'balanced': 'claude-sonnet-4-6',
        'deep': 'claude-opus-4-6',
    }

    if request.content_type and 'multipart/form-data' in request.content_type:
        user_message = request.form.get('message', '').strip()
        model_choice = request.form.get('model', 'balanced')
        session_history_raw = request.form.get('session_history', '[]')

        # Support multiple images
        image_files = request.files.getlist('images') or []
        # Also check legacy single 'image' field
        single = request.files.get('image')
        if single and single.filename:
            image_files.append(single)

        from blueprints.uploads import save_chat_image
        for image_file in image_files:
            if not image_file or not image_file.filename:
                continue
            fname, original_path = save_chat_image(image_file)
            if not fname or not original_path:
                continue
            if not image_filename:
                image_filename = fname  # store first for DB
            with open(original_path, 'rb') as f:
                raw = f.read()
            img_media_type = None
            if len(raw) > 4 * 1024 * 1024:
                try:
                    from PIL import Image
                    import io
                    img = Image.open(io.BytesIO(raw))
                    img.thumbnail((2048, 2048))
                    buf = io.BytesIO()
                    img.save(buf, format='JPEG', quality=85)
                    raw = buf.getvalue()
                    img_media_type = 'image/jpeg'
                except Exception:
                    pass
            encoded = base64.b64encode(raw).decode('utf-8')
            ext = fname.rsplit('.', 1)[-1].lower()
            if not img_media_type:
                img_media_type = {
                    'jpg': 'image/jpeg', 'jpeg': 'image/jpeg',
                    'png': 'image/png', 'gif': 'image/gif',
                    'webp': 'image/webp',
                }.get(ext, 'image/jpeg')
            images.append({'data': encoded, 'media_type': img_media_type, 'filename': fname})
    else:
        data = request.get_json() or {}
        user_message = data.get('message', '').strip()
        model_choice = data.get('model', 'balanced')
        session_history_raw = data.get('session_history', [])

    if not user_message and not images:
        return jsonify({'error': 'Empty message'}), 400

    if not user_message and images:
        user_message = "Please analyze this travel document and extract any useful information. Match it to existing accommodations, flights, or activities if possible."

    # Save user message
    user_msg = ChatMessage(
        role='user',
        content=user_message,
        image_filename=image_filename,
    )
    db.session.add(user_msg)
    db.session.commit()

    # Build context
    context = build_context()

    # Build messages from client-sent session history
    messages = []
    if isinstance(session_history_raw, str):
        try:
            session_history_raw = json.loads(session_history_raw)
        except (json.JSONDecodeError, TypeError):
            session_history_raw = []

    for m in (session_history_raw or []):
        if isinstance(m, dict) and m.get('role') and m.get('content'):
            messages.append({'role': m['role'], 'content': m['content']})

    # Build current user message with optional images
    user_content = []
    for img in images:
        user_content.append({
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": img['media_type'],
                "data": img['data'],
            }
        })
    user_content.append({"type": "text", "text": user_message})
    messages.append({"role": "user", "content": user_content})

    model_id = MODEL_MAP.get(model_choice, 'claude-sonnet-4-6')
    # Token budgets per tier
    if model_choice == 'deep':
        max_tokens = 16384
    elif model_choice == 'balanced':
        max_tokens = 8192
    else:
        max_tokens = 2048
    app = current_app._get_current_object()

    def generate():
        try:
            import anthropic
            client = anthropic.Anthropic(api_key=api_key)
            system = SYSTEM_PROMPT + '\n\n' + context
            full_response = ''

            # Configure thinking for balanced/deep modes (adaptive for 4.6 models)
            use_thinking = model_choice in ('balanced', 'deep')

            # Combine user-defined and server-side tools
            all_tools = TOOLS + SERVER_TOOLS

            base_kwargs = dict(
                model=model_id,
                max_tokens=max_tokens,
                system=system,
                tools=all_tools,
            )
            if use_thinking:
                base_kwargs['thinking'] = {"type": "adaptive"}

            # Multi-round tool loop: up to MAX_TOOL_ROUNDS rounds of tool use
            tool_results = []
            text_parts = []

            for _round in range(MAX_TOOL_ROUNDS):
                api_kwargs = dict(base_kwargs, messages=messages)
                response = _api_call_with_retry(client, **api_kwargs)

                # Process response blocks
                tool_results = []
                text_parts = []
                for block in response.content:
                    if block.type == 'tool_use':
                        yield f"data: {json.dumps({'processing': f'Updating: {block.name}...'})}\n\n"
                        with app.app_context():
                            result = execute_tool(block.name, block.input)
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": json.dumps(result),
                        })
                    elif block.type == 'server_tool_use':
                        yield f"data: {json.dumps({'processing': 'Searching the web...'})}\n\n"
                    elif block.type == 'text':
                        text_parts.append(block.text)
                    elif block.type == 'thinking':
                        yield f"data: {json.dumps({'processing': 'Thinking...'})}\n\n"

                if not tool_results:
                    # Final response — no more tools needed
                    full_response = ''.join(text_parts)
                    yield f"data: {json.dumps({'text': full_response})}\n\n"
                    break

                # Append assistant content + tool results, continue loop
                assistant_content = []
                for block in response.content:
                    if block.type == 'text':
                        assistant_content.append({"type": "text", "text": block.text})
                    elif block.type == 'tool_use':
                        assistant_content.append({
                            "type": "tool_use",
                            "id": block.id,
                            "name": block.name,
                            "input": block.input,
                        })
                    elif block.type == 'thinking':
                        assistant_content.append({
                            "type": "thinking",
                            "thinking": block.thinking,
                            "signature": block.signature,
                        })
                messages.append({"role": "assistant", "content": assistant_content})
                messages.append({"role": "user", "content": tool_results})

                yield f"data: {json.dumps({'processing': 'Processing results...'})}\n\n"
            else:
                # Exhausted all rounds but last round still had tool calls
                # Yield any text we collected from the last round
                if text_parts:
                    full_response = ''.join(text_parts)
                    yield f"data: {json.dumps({'text': full_response})}\n\n"

            # If the last round had tool results, do one final streaming call for summary
            # Use tool_choice=none to prevent the model from requesting more tools
            # in the summary — those would be silently dropped by the stream reader.
            if tool_results:
                followup_kwargs = dict(base_kwargs, messages=messages,
                                       tool_choice={"type": "none"})
                with client.messages.stream(**followup_kwargs) as stream:
                    for text in stream.text_stream:
                        full_response += text
                        yield f"data: {json.dumps({'text': text})}\n\n"

            # Save assistant response
            with app.app_context():
                assistant_msg = ChatMessage(role='assistant', content=full_response)
                db.session.add(assistant_msg)
                db.session.commit()
            yield f"data: {json.dumps({'done': True})}\n\n"

        except Exception as e:
            yield f"data: {json.dumps({'error': str(e)})}\n\n"

    return Response(generate(), mimetype='text/event-stream')


@chat_bp.route('/api/chat/history')
def chat_history():
    messages = ChatMessage.query.order_by(
        ChatMessage.created_at.desc()).limit(50).all()
    messages.reverse()
    return jsonify([{
        'role': m.role,
        'content': m.content,
        'image_filename': m.image_filename,
        'created_at': m.created_at.isoformat() if m.created_at else None,
    } for m in messages])


@chat_bp.route('/api/chat/history', methods=['DELETE'])
def clear_chat_history():
    """Clear all chat messages from the server."""
    ChatMessage.query.delete()
    db.session.commit()
    return jsonify({'success': True, 'message': 'Chat history cleared'})
