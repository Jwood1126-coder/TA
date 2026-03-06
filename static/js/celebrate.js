// Pikachu Booking Celebration — fully self-contained (no external CSS dependency)

const PIKACHU_SVG = `
<svg viewBox="0 0 240 280" class="pika-svg" xmlns="http://www.w3.org/2000/svg">
  <style>
    .pika-svg { animation: pikaBounce 0.4s ease-in-out infinite alternate; }
    @keyframes pikaBounce { 0%{transform:translateY(0) rotate(-2deg)} 100%{transform:translateY(-10px) rotate(2deg)} }
    .pika-foot-l { transform-origin:90px 255px; animation: footL 0.3s ease-in-out infinite alternate; }
    .pika-foot-r { transform-origin:150px 255px; animation: footR 0.3s ease-in-out infinite alternate-reverse; }
    @keyframes footL { 0%{transform:translateY(0) rotate(0)} 100%{transform:translateY(-5px) rotate(-6deg)} }
    @keyframes footR { 0%{transform:translateY(-5px) rotate(6deg)} 100%{transform:translateY(0) rotate(0)} }
    .pika-arm-r { transform-origin:172px 165px; animation: armWave 0.35s ease-in-out infinite alternate; }
    .pika-arm-l { transform-origin:68px 165px; animation: armSwL 0.4s ease-in-out infinite alternate; }
    @keyframes armWave { 0%{transform:rotate(0)} 100%{transform:rotate(-30deg)} }
    @keyframes armSwL { 0%{transform:rotate(0)} 100%{transform:rotate(10deg)} }
    .pika-tail { transform-origin:185px 180px; animation: tailWag 0.3s ease-in-out infinite alternate; }
    @keyframes tailWag { 0%{transform:rotate(-8deg)} 100%{transform:rotate(8deg)} }
    .pika-cheek { animation: cheekGlow 0.8s ease-in-out infinite alternate; }
    @keyframes cheekGlow { 0%{opacity:0.6} 100%{opacity:0.9} }
    .pika-sparkle { animation: sparkle 1.2s ease-in-out infinite alternate; }
    .s1{animation-delay:0s} .s2{animation-delay:.2s} .s3{animation-delay:.4s}
    .s4{animation-delay:.15s} .s5{animation-delay:.35s} .s6{animation-delay:.55s}
    @keyframes sparkle { 0%{opacity:.3;transform:translateY(0) scale(.8)} 100%{opacity:1;transform:translateY(-8px) scale(1.3)} }
  </style>
  <!-- Tail (behind body) — proper lightning bolt shape -->
  <g class="pika-tail">
    <polygon points="185,180 210,140 198,140 220,95 195,95 218,48 175,120 192,120 172,165"
             fill="#F5D442" stroke="#333" stroke-width="2.5" stroke-linejoin="round"/>
    <!-- Brown base of tail -->
    <polygon points="185,180 172,165 178,172" fill="#8B6914" stroke="#333" stroke-width="1.5"/>
  </g>
  <!-- Body — rounder, chubbier -->
  <ellipse cx="120" cy="195" rx="58" ry="55" fill="#F5D442" stroke="#333" stroke-width="2.5"/>
  <!-- Belly lighter patch -->
  <ellipse cx="120" cy="205" rx="34" ry="30" fill="#FFF3B0" opacity="0.5"/>
  <!-- Left arm -->
  <g class="pika-arm-l">
    <path d="M68 165 Q42 170 40 190 Q38 200 50 200 Q58 198 68 185" fill="#F5D442" stroke="#333" stroke-width="2.5" stroke-linejoin="round"/>
  </g>
  <!-- Right arm (waving!) -->
  <g class="pika-arm-r">
    <path d="M172 165 Q198 150 202 165 Q206 178 192 182 Q180 183 172 175" fill="#F5D442" stroke="#333" stroke-width="2.5" stroke-linejoin="round"/>
  </g>
  <!-- Left foot -->
  <g class="pika-foot-l">
    <ellipse cx="90" cy="248" rx="22" ry="12" fill="#F5D442" stroke="#333" stroke-width="2.5"/>
    <ellipse cx="90" cy="250" rx="22" ry="10" fill="#F5D442"/>
  </g>
  <!-- Right foot -->
  <g class="pika-foot-r">
    <ellipse cx="150" cy="248" rx="22" ry="12" fill="#F5D442" stroke="#333" stroke-width="2.5"/>
    <ellipse cx="150" cy="250" rx="22" ry="10" fill="#F5D442"/>
  </g>
  <!-- Head — wider, rounder, Pikachu-shaped -->
  <ellipse cx="120" cy="105" rx="68" ry="60" fill="#F5D442" stroke="#333" stroke-width="2.5"/>
  <!-- Brown back stripes on head -->
  <path d="M82 68 Q90 80 82 90" fill="none" stroke="#8B6914" stroke-width="4" stroke-linecap="round" opacity="0.7"/>
  <path d="M158 68 Q150 80 158 90" fill="none" stroke="#8B6914" stroke-width="4" stroke-linecap="round" opacity="0.7"/>
  <!-- Left ear — long, pointed, with black tip -->
  <path d="M65 75 L40 8 L82 58" fill="#F5D442" stroke="#333" stroke-width="2.5" stroke-linejoin="round"/>
  <path d="M40 8 L52 30 L64 24" fill="#333"/>
  <!-- Right ear — long, pointed, with black tip -->
  <path d="M175 75 L200 8 L158 58" fill="#F5D442" stroke="#333" stroke-width="2.5" stroke-linejoin="round"/>
  <path d="M200 8 L188 30 L176 24" fill="#333"/>
  <!-- Eyes — large, round, expressive -->
  <ellipse cx="95" cy="98" rx="11" ry="14" fill="#333"/>
  <ellipse cx="145" cy="98" rx="11" ry="14" fill="#333"/>
  <!-- Eye highlights — big gleam -->
  <ellipse cx="99" cy="92" rx="5" ry="6" fill="#fff"/>
  <ellipse cx="149" cy="92" rx="5" ry="6" fill="#fff"/>
  <!-- Small lower eye highlight -->
  <ellipse cx="92" cy="102" rx="2.5" ry="2.5" fill="#fff" opacity="0.6"/>
  <ellipse cx="142" cy="102" rx="2.5" ry="2.5" fill="#fff" opacity="0.6"/>
  <!-- Red cheeks — round circles -->
  <circle class="pika-cheek" cx="62" cy="115" r="14" fill="#E44" opacity="0.75"/>
  <circle class="pika-cheek" cx="178" cy="115" r="14" fill="#E44" opacity="0.75"/>
  <!-- Nose — tiny triangle -->
  <path d="M117 108 L123 108 L120 112 Z" fill="#333"/>
  <!-- Mouth — happy wide smile -->
  <path d="M100 120 Q110 134 120 128 Q130 134 140 120" fill="none" stroke="#333" stroke-width="2.5" stroke-linecap="round"/>
  <!-- Open happy mouth interior -->
  <path d="M105 122 Q120 138 135 122" fill="#A03030" stroke="none"/>
  <!-- Tongue -->
  <ellipse cx="120" cy="130" rx="8" ry="5" fill="#E07070"/>
  <!-- Sparkles around -->
  <g>
    <text x="15" y="45" font-size="20" class="pika-sparkle s1">&#x26A1;</text>
    <text x="210" y="35" font-size="18" class="pika-sparkle s2">&#x2728;</text>
    <text x="5" y="150" font-size="16" class="pika-sparkle s3">&#x2B50;</text>
    <text x="220" y="170" font-size="20" class="pika-sparkle s4">&#x26A1;</text>
    <text x="30" y="240" font-size="14" class="pika-sparkle s5">&#x2728;</text>
    <text x="200" y="250" font-size="16" class="pika-sparkle s6">&#x2B50;</text>
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
            <div class="pika-title">ありがとう ありがとう!</div>
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
