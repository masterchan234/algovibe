/* Pull-to-Refresh Logic for AlgoVibe Mobile (Premium Edition) */
(function() {
    let startY = 0;
    let isPulling = false;
    const threshold = 120;
    let refreshIndicator = null;
    let hasVibrated = false;

    const style = document.createElement('style');
    style.textContent = `
        #ptr-indicator {
            position: fixed;
            top: -60px;
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
            transition: opacity 0.2s, top 0.1s;
            opacity: 0;
        }
        #ptr-indicator svg {
            width: 24px;
            height: 24px;
            fill: white;
            transition: transform 0.1s;
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

    document.addEventListener('touchstart', (e) => {
        const scrollTarget = document.documentElement;
        if (window.scrollY <= 5) {
            startY = e.touches[0].pageY;
            createIndicator();
            hasVibrated = false;
        } else {
            startY = 0;
        }
    }, { passive: true });

    document.addEventListener('touchmove', (e) => {
        if (startY === 0) return;
        
        const y = e.touches[0].pageY;
        const pull = y - startY;
        
        if (pull > 0 && isPulling === false) isPulling = true;

        if (pull > 0) {
            const progress = Math.min(pull / threshold, 1);
            refreshIndicator.style.top = `${Math.min(pull * 0.5, 80)}px`;
            refreshIndicator.style.opacity = progress;
            refreshIndicator.querySelector('svg').style.transform = `rotate(${pull * 2}deg)`;
            
            if (pull > threshold && !hasVibrated) {
                if (navigator.vibrate) navigator.vibrate(15);
                hasVibrated = true;
                refreshIndicator.style.background = '#10b981'; // Green on ready
            } else if (pull <= threshold) {
                hasVibrated = false;
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
                if (navigator.vibrate) navigator.vibrate([10, 30, 10]);
                setTimeout(() => location.reload(), 600);
            } else {
                refreshIndicator.style.top = '-60px';
                refreshIndicator.style.opacity = '0';
            }
        }
        startY = 0;
        isPulling = false;
    }, { passive: true });
})();
