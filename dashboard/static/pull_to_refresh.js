/* Pull-to-Refresh Logic for AlgoVibe Mobile (Smart Edition) */
(function() {
    let startY = 0;
    let startX = 0;
    let isPulling = false;
    let isScrollingContent = false;
    const threshold = 120;
    let refreshIndicator = null;
    let hasVibrated = false;

    const style = document.createElement('style');
    style.textContent = `
        #ptr-indicator {
            position: fixed;
            top: -70px;
            left: 50%;
            transform: translateX(-50%);
            width: 45px;
            height: 45px;
            background: rgba(37, 99, 235, 0.9);
            backdrop-filter: blur(10px);
            border: 1px solid rgba(255,255,255,0.2);
            border-radius: 50%;
            display: flex;
            align-items: center;
            justify-content: center;
            z-index: 9999;
            box-shadow: 0 4px 15px rgba(0,0,0,0.4), 0 0 10px rgba(37, 99, 235, 0.4);
            transition: top 0.15s ease-out, opacity 0.2s, background 0.2s;
            opacity: 0;
            pointer-events: none;
        }
        #ptr-indicator svg {
            width: 24px;
            height: 24px;
            fill: white;
            transition: transform 0.05s linear;
        }
        #ptr-indicator.refreshing svg {
            animation: ptr-rotate 0.8s linear infinite;
        }
        @keyframes ptr-rotate {
            from { transform: rotate(0deg); }
            to { transform: rotate(360deg); }
        }
    `;
    document.head.appendChild(style);

    function createIndicator() {
        if (refreshIndicator) return;
        refreshIndicator = document.createElement('div');
        refreshIndicator.id = 'ptr-indicator';
        refreshIndicator.innerHTML = `
            <svg viewBox="0 0 24 24">
                <path d="M17.65 6.35C16.2 4.9 14.21 4 12 4c-4.42 0-7.99 3.58-7.99 8s3.57 8 7.99 8c3.73 0 6.84-2.55 7.73-6h-2.08c-.82 2.33-3.04 4-5.65 4-3.31 0-6-2.69-6-6s2.69-6 6-6c1.66 0 3.14.69 4.22 1.78L13 11h7V4l-2.35 2.35z"/>
            </svg>
        `;
        document.body.prepend(refreshIndicator);
    }

    function getPageScrollTop() {
        // Cek main scrollable container dulu, fallback ke window
        const container = document.querySelector('.center-area') || 
                          document.querySelector('main') || 
                          null;
        if (container && container.scrollHeight > container.clientHeight) {
            return container.scrollTop;
        }
        return window.scrollY || document.documentElement.scrollTop;
    }

    function resetPTR() {
        if (refreshIndicator) {
            refreshIndicator.style.top = '-70px';
            refreshIndicator.style.opacity = '0';
            refreshIndicator.style.background = 'rgba(37, 99, 235, 0.9)';
        }
        startY = 0;
        startX = 0;
        isPulling = false;
        isScrollingContent = false;
        hasVibrated = false;
    }

    document.addEventListener('touchstart', (e) => {
        // Reset semua state
        isPulling = false;
        isScrollingContent = false;
        hasVibrated = false;
        startY = e.touches[0].pageY;
        startX = e.touches[0].pageX;
    }, { passive: true });

    document.addEventListener('touchmove', (e) => {
        if (startY === 0) return;

        const y = e.touches[0].pageY;
        const x = e.touches[0].pageX;
        const deltaY = y - startY;
        const deltaX = x - startX;

        // Jika gerakan lebih horizontal dari vertikal → bukan PTR, abort
        if (!isPulling && !isScrollingContent) {
            if (Math.abs(deltaX) > Math.abs(deltaY)) {
                isScrollingContent = true;
                return;
            }
        }

        if (isScrollingContent) return;

        // PTR hanya aktif jika:
        // 1. Halaman benar-benar di posisi teratas (scrollTop <= 0)
        // 2. Gerakan ke bawah (deltaY > 0)
        const scrollTop = getPageScrollTop();

        if (scrollTop > 2) {
            // User sedang scroll konten, bukan PTR
            isScrollingContent = true;
            return;
        }

        if (deltaY > 5) {
            isPulling = true;
            createIndicator();

            const progress = Math.min(deltaY / threshold, 1);
            const topPos = Math.min(deltaY * 0.45, 80);

            refreshIndicator.style.top = `${topPos}px`;
            refreshIndicator.style.opacity = Math.min(progress, 1);
            refreshIndicator.querySelector('svg').style.transform = `rotate(${deltaY * 2}deg)`;

            if (deltaY > threshold && !hasVibrated) {
                if (navigator.vibrate) navigator.vibrate(15);
                hasVibrated = true;
                refreshIndicator.style.background = '#10b981';
            } else if (deltaY <= threshold) {
                if (hasVibrated) hasVibrated = false;
                refreshIndicator.style.background = 'rgba(37, 99, 235, 0.9)';
            }
        }
    }, { passive: true });

    document.addEventListener('touchend', (e) => {
        if (isPulling && refreshIndicator) {
            const pull = e.changedTouches[0].pageY - startY;
            if (pull > threshold) {
                refreshIndicator.classList.add('refreshing');
                refreshIndicator.style.top = '70px';
                refreshIndicator.style.opacity = '1';
                if (navigator.vibrate) navigator.vibrate([10, 30, 10]);
                setTimeout(() => location.reload(), 600);
                return; // Jangan reset, biarkan loading
            }
        }
        resetPTR();
    }, { passive: true });

    document.addEventListener('touchcancel', () => {
        resetPTR();
    }, { passive: true });

})();
