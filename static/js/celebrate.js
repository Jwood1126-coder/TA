// Pikachu Booking Celebration — fully self-contained (no external CSS dependency)

const PIKACHU_SVG = `
<svg viewBox="0 0 200 220" class="pika-svg" xmlns="http://www.w3.org/2000/svg">
  <style>
    .pika-svg { animation: pikaBounce 0.4s ease-in-out infinite alternate; }
    @keyframes pikaBounce { 0%{transform:translateY(0) rotate(-2deg)} 100%{transform:translateY(-10px) rotate(2deg)} }
    .pika-foot-l { transform-origin:78px 205px; animation: footL 0.3s ease-in-out infinite alternate; }
    .pika-foot-r { transform-origin:122px 205px; animation: footR 0.3s ease-in-out infinite alternate; }
    @keyframes footL { 0%{transform:translateY(0) rotate(0)} 100%{transform:translateY(-4px) rotate(-5deg)} }
    @keyframes footR { 0%{transform:translateY(-4px) rotate(5deg)} 100%{transform:translateY(0) rotate(0)} }
    .pika-arm-r { transform-origin:145px 145px; animation: armWave 0.35s ease-in-out infinite alternate; }
    .pika-arm-l { transform-origin:55px 145px; animation: armSwL 0.4s ease-in-out infinite alternate; }
    @keyframes armWave { 0%{transform:rotate(0)} 100%{transform:rotate(-25deg)} }
    @keyframes armSwL { 0%{transform:rotate(0)} 100%{transform:rotate(8deg)} }
    .pika-tail { transform-origin:150px 155px; animation: tailWag 0.3s ease-in-out infinite alternate; }
    @keyframes tailWag { 0%{transform:rotate(-5deg)} 100%{transform:rotate(5deg)} }
    .pika-cheek { animation: cheekGlow 0.8s ease-in-out infinite alternate; }
    @keyframes cheekGlow { 0%{opacity:0.5} 100%{opacity:0.85} }
    .pika-sparkle { animation: sparkle 1.2s ease-in-out infinite alternate; }
    .s1{animation-delay:0s} .s2{animation-delay:.2s} .s3{animation-delay:.4s}
    .s4{animation-delay:.15s} .s5{animation-delay:.35s} .s6{animation-delay:.55s}
    @keyframes sparkle { 0%{opacity:.3;transform:translateY(0) scale(.8)} 100%{opacity:1;transform:translateY(-8px) scale(1.3)} }
  </style>
  <path d="M55 10 L38 75 L72 65 Z" fill="#F5D442" stroke="#333" stroke-width="2"/>
  <path d="M55 10 L48 40 L62 35 Z" fill="#333"/>
  <path d="M145 10 L128 65 L162 75 Z" fill="#F5D442" stroke="#333" stroke-width="2"/>
  <path d="M145 10 L138 35 L152 40 Z" fill="#333"/>
  <ellipse cx="100" cy="95" rx="62" ry="55" fill="#F5D442" stroke="#333" stroke-width="2"/>
  <ellipse cx="78" cy="85" rx="8" ry="10" fill="#333"/>
  <ellipse cx="122" cy="85" rx="8" ry="10" fill="#333"/>
  <ellipse cx="80" cy="82" rx="3" ry="3.5" fill="#fff"/>
  <ellipse cx="124" cy="82" rx="3" ry="3.5" fill="#fff"/>
  <ellipse class="pika-cheek" cx="58" cy="102" rx="12" ry="9" fill="#E55" opacity="0.7"/>
  <ellipse class="pika-cheek" cx="142" cy="102" rx="12" ry="9" fill="#E55" opacity="0.7"/>
  <ellipse cx="100" cy="93" rx="3" ry="2" fill="#333"/>
  <path d="M85 105 Q100 120 115 105" fill="none" stroke="#333" stroke-width="2.5" stroke-linecap="round"/>
  <ellipse cx="100" cy="165" rx="50" ry="45" fill="#F5D442" stroke="#333" stroke-width="2"/>
  <ellipse cx="100" cy="170" rx="30" ry="25" fill="#FFF8DC" opacity="0.5"/>
  <g class="pika-arm-l"><path d="M55 145 Q30 155 35 175 Q40 185 55 178" fill="#F5D442" stroke="#333" stroke-width="2"/></g>
  <g class="pika-arm-r"><path d="M145 145 Q170 130 175 145 Q178 155 160 160" fill="#F5D442" stroke="#333" stroke-width="2"/></g>
  <g class="pika-foot-l"><ellipse cx="78" cy="205" rx="18" ry="10" fill="#F5D442" stroke="#333" stroke-width="2"/></g>
  <g class="pika-foot-r"><ellipse cx="122" cy="205" rx="18" ry="10" fill="#F5D442" stroke="#333" stroke-width="2"/></g>
  <g class="pika-tail"><path d="M150 155 L170 130 L160 130 L178 100 L155 125 L165 125 L148 148" fill="#F5D442" stroke="#333" stroke-width="2" stroke-linejoin="round"/></g>
  <g>
    <text x="20" y="50" font-size="18" class="pika-sparkle s1">&#x26A1;</text>
    <text x="170" y="40" font-size="16" class="pika-sparkle s2">&#x2728;</text>
    <text x="10" y="130" font-size="14" class="pika-sparkle s3">&#x2B50;</text>
    <text x="180" y="150" font-size="18" class="pika-sparkle s4">&#x26A1;</text>
    <text x="50" y="190" font-size="12" class="pika-sparkle s5">&#x2728;</text>
    <text x="155" y="200" font-size="14" class="pika-sparkle s6">&#x2B50;</text>
  </g>
</svg>`;

function playBookingTune() {
    try {
        const ctx = new (window.AudioContext || window.webkitAudioContext)();
        const notes = [
            { freq: 523.25, start: 0,    dur: 0.15 },
            { freq: 659.25, start: 0.15, dur: 0.15 },
            { freq: 783.99, start: 0.30, dur: 0.15 },
            { freq: 1046.5, start: 0.45, dur: 0.25 },
            { freq: 783.99, start: 0.72, dur: 0.12 },
            { freq: 1046.5, start: 0.85, dur: 0.35 },
            { freq: 1318.5, start: 1.25, dur: 0.08 },
            { freq: 1174.7, start: 1.35, dur: 0.08 },
            { freq: 1046.5, start: 1.45, dur: 0.08 },
            { freq: 987.77, start: 1.55, dur: 0.08 },
            { freq: 1046.5, start: 1.65, dur: 0.4  },
        ];
        notes.forEach(({ freq, start, dur }) => {
            const osc = ctx.createOscillator();
            const gain = ctx.createGain();
            osc.type = 'square';
            osc.frequency.value = freq;
            gain.gain.setValueAtTime(0.08, ctx.currentTime + start);
            gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + start + dur);
            osc.connect(gain);
            gain.connect(ctx.destination);
            osc.start(ctx.currentTime + start);
            osc.stop(ctx.currentTime + start + dur + 0.05);
        });
        setTimeout(() => ctx.close(), 3000);
    } catch (e) {}
}

function showBookingCelebration(hotelName) {
    // Remove any existing celebration
    document.querySelectorAll('.pika-celebrate').forEach(el => el.remove());

    // Inject scoped styles directly (cache-proof — no external CSS needed)
    if (!document.getElementById('pikaStyles')) {
        const style = document.createElement('style');
        style.id = 'pikaStyles';
        style.textContent = `
            .pika-celebrate {
                position: fixed; z-index: 99999;
                top: 0; left: 0; right: 0; bottom: 0;
                display: flex; align-items: center; justify-content: center;
                pointer-events: none;
            }
            .pika-celebrate.visible { pointer-events: auto; }
            .pika-bubble {
                position: relative;
                display: flex; flex-direction: column; align-items: center;
                background: rgba(0,0,0,0.6);
                border-radius: 24px;
                padding: 1.5rem 2rem 1.2rem;
                box-shadow: 0 12px 48px rgba(0,0,0,0.4);
                opacity: 0;
                transform: translateY(100vh) scale(0.4);
                transition: transform 0.6s cubic-bezier(0.34,1.56,0.64,1), opacity 0.3s ease;
            }
            .pika-celebrate.visible .pika-bubble {
                opacity: 1;
                transform: translateY(0) scale(1);
            }
            .pika-celebrate.bye .pika-bubble {
                opacity: 0;
                transform: translateY(-60vh) scale(0.6) rotate(8deg);
                transition: transform 0.6s ease-in, opacity 0.4s ease-in;
            }
            .pika-svg {
                width: 40vw; max-width: 200px; height: auto;
                filter: drop-shadow(0 4px 16px rgba(245,212,66,0.5));
            }
            .pika-title {
                font-size: 2rem; font-weight: 900; color: #F5D442;
                text-shadow: 2px 2px 0 #333, -1px -1px 0 #333, 0 4px 20px rgba(245,212,66,0.6);
                letter-spacing: 2px; text-align: center; margin-top: 0.6rem;
                animation: pikaTitle 0.6s ease-in-out infinite alternate;
            }
            @keyframes pikaTitle { 0%{transform:scale(1)} 100%{transform:scale(1.08)} }
            .pika-sub {
                font-size: 1rem; color: #fff; text-align: center;
                margin-top: 0.2rem; font-weight: 600;
                text-shadow: 0 2px 8px rgba(0,0,0,0.5);
            }
            .pika-x {
                position: absolute; top: 8px; right: 8px;
                width: 32px; height: 32px; border: none;
                background: rgba(255,255,255,0.2); color: #fff;
                font-size: 1.4rem; border-radius: 50%; cursor: pointer;
                display: flex; align-items: center; justify-content: center;
                opacity: 0; animation: pikaXShow 0.3s ease-out 1.5s forwards;
            }
            @keyframes pikaXShow { to { opacity: 1; } }
        `;
        document.head.appendChild(style);
    }

    const el = document.createElement('div');
    el.className = 'pika-celebrate';
    el.innerHTML = `
        <div class="pika-bubble">
            ${PIKACHU_SVG}
            <div class="pika-title">PIKA PIKA!</div>
            <div class="pika-sub">${hotelName || 'Hotel'} is booked!</div>
            <button class="pika-x" aria-label="Close">&times;</button>
        </div>
    `;

    // Block events from reaching the page
    ['mousedown','touchstart','click'].forEach(evt => {
        el.addEventListener(evt, e => e.stopPropagation(), true);
    });

    document.body.appendChild(el);

    // Animate in (double rAF ensures browser paints offscreen state first)
    requestAnimationFrame(() => {
        requestAnimationFrame(() => {
            el.classList.add('visible');
            playBookingTune();
        });
    });

    // Auto dismiss
    const timer = setTimeout(dismiss, 4500);

    function dismiss() {
        if (el.classList.contains('bye')) return;
        clearTimeout(timer);
        el.classList.remove('visible');
        el.classList.add('bye');
        setTimeout(() => el.remove(), 700);
    }

    // Only the X button dismisses
    el.querySelector('.pika-x').addEventListener('click', e => {
        e.stopPropagation();
        dismiss();
    });
}

window.showBookingCelebration = showBookingCelebration;
