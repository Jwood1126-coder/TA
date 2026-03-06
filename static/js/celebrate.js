// Pikachu Booking Celebration
// Slide-in dancing Pikachu with chiptune — app stays visible behind

const PIKACHU_SVG = `
<svg viewBox="0 0 200 220" class="pikachu-svg" xmlns="http://www.w3.org/2000/svg">
  <!-- Left Ear -->
  <path d="M55 10 L38 75 L72 65 Z" fill="#F5D442" stroke="#333" stroke-width="2"/>
  <path d="M55 10 L48 40 L62 35 Z" fill="#333"/>
  <!-- Right Ear -->
  <path d="M145 10 L128 65 L162 75 Z" fill="#F5D442" stroke="#333" stroke-width="2"/>
  <path d="M145 10 L138 35 L152 40 Z" fill="#333"/>
  <!-- Head -->
  <ellipse cx="100" cy="95" rx="62" ry="55" fill="#F5D442" stroke="#333" stroke-width="2"/>
  <!-- Eyes -->
  <ellipse cx="78" cy="85" rx="8" ry="10" fill="#333"/>
  <ellipse cx="122" cy="85" rx="8" ry="10" fill="#333"/>
  <ellipse cx="80" cy="82" rx="3" ry="3.5" fill="#fff"/>
  <ellipse cx="124" cy="82" rx="3" ry="3.5" fill="#fff"/>
  <!-- Cheeks -->
  <ellipse cx="58" cy="102" rx="12" ry="9" fill="#E55" opacity="0.7"/>
  <ellipse cx="142" cy="102" rx="12" ry="9" fill="#E55" opacity="0.7"/>
  <!-- Nose -->
  <ellipse cx="100" cy="93" rx="3" ry="2" fill="#333"/>
  <!-- Mouth (happy!) -->
  <path d="M85 105 Q100 120 115 105" fill="none" stroke="#333" stroke-width="2.5" stroke-linecap="round"/>
  <!-- Body -->
  <ellipse cx="100" cy="165" rx="50" ry="45" fill="#F5D442" stroke="#333" stroke-width="2"/>
  <!-- Belly -->
  <ellipse cx="100" cy="170" rx="30" ry="25" fill="#FFF8DC" opacity="0.5"/>
  <!-- Left Arm -->
  <g class="pikachu-arm-left">
    <path d="M55 145 Q30 155 35 175 Q40 185 55 178" fill="#F5D442" stroke="#333" stroke-width="2"/>
  </g>
  <!-- Right Arm (waving!) -->
  <g class="pikachu-arm-wave">
    <path d="M145 145 Q170 130 175 145 Q178 155 160 160" fill="#F5D442" stroke="#333" stroke-width="2"/>
  </g>
  <!-- Left Foot -->
  <g class="pikachu-foot-left">
    <ellipse cx="78" cy="205" rx="18" ry="10" fill="#F5D442" stroke="#333" stroke-width="2"/>
  </g>
  <!-- Right Foot -->
  <g class="pikachu-foot-right">
    <ellipse cx="122" cy="205" rx="18" ry="10" fill="#F5D442" stroke="#333" stroke-width="2"/>
  </g>
  <!-- Tail (lightning bolt!) -->
  <g class="pikachu-tail">
    <path d="M150 155 L170 130 L160 130 L178 100 L155 125 L165 125 L148 148" fill="#F5D442" stroke="#333" stroke-width="2" stroke-linejoin="round"/>
  </g>
  <!-- Sparkle effects -->
  <g class="sparkles">
    <text x="20" y="50" font-size="18" class="sparkle s1">&#x26A1;</text>
    <text x="170" y="40" font-size="16" class="sparkle s2">&#x2728;</text>
    <text x="10" y="130" font-size="14" class="sparkle s3">&#x2B50;</text>
    <text x="180" y="150" font-size="18" class="sparkle s4">&#x26A1;</text>
    <text x="50" y="190" font-size="12" class="sparkle s5">&#x2728;</text>
    <text x="155" y="200" font-size="14" class="sparkle s6">&#x2B50;</text>
  </g>
</svg>`;

function playBookingTune() {
    try {
        const ctx = new (window.AudioContext || window.webkitAudioContext)();
        const notes = [
            { freq: 523.25, start: 0,    dur: 0.15 },  // C5
            { freq: 659.25, start: 0.15, dur: 0.15 },  // E5
            { freq: 783.99, start: 0.30, dur: 0.15 },  // G5
            { freq: 1046.5, start: 0.45, dur: 0.25 },  // C6
            { freq: 783.99, start: 0.72, dur: 0.12 },  // G5
            { freq: 1046.5, start: 0.85, dur: 0.35 },  // C6 (held)
            // Sparkle descend
            { freq: 1318.5, start: 1.25, dur: 0.08 },  // E6
            { freq: 1174.7, start: 1.35, dur: 0.08 },  // D6
            { freq: 1046.5, start: 1.45, dur: 0.08 },  // C6
            { freq: 987.77, start: 1.55, dur: 0.08 },  // B5
            { freq: 1046.5, start: 1.65, dur: 0.4  },  // C6 (final)
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
    } catch (e) {
        // Audio not supported
    }
}

function showBookingCelebration(hotelName) {
    // Floating container — no background overlay, app stays visible
    const container = document.createElement('div');
    container.className = 'celebrate-float';
    container.innerHTML = `
        <div class="celebrate-bubble">
            ${PIKACHU_SVG}
            <div class="celebrate-text">
                <div class="celebrate-title">PIKA PIKA!</div>
                <div class="celebrate-subtitle">${hotelName || 'Hotel'} is booked!</div>
            </div>
        </div>
    `;
    document.body.appendChild(container);

    // Trigger slide-in + dance
    requestAnimationFrame(() => {
        requestAnimationFrame(() => {
            container.classList.add('active');
            playBookingTune();
        });
    });

    // Auto dismiss after 4s
    const dismissTimer = setTimeout(dismiss, 4000);

    // Tap to dismiss (with guard against immediate phantom tap)
    let canDismiss = false;
    setTimeout(() => { canDismiss = true; }, 800);

    function dismiss() {
        if (container.classList.contains('leaving')) return;
        clearTimeout(dismissTimer);
        container.classList.add('leaving');
        setTimeout(() => container.remove(), 700);
    }

    container.addEventListener('click', () => {
        if (canDismiss) dismiss();
    });
    container.addEventListener('touchend', (e) => {
        if (canDismiss) {
            e.preventDefault();
            dismiss();
        }
    });
}

window.showBookingCelebration = showBookingCelebration;
