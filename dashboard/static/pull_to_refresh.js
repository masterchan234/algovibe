/* Pull-to-Refresh Logic for AlgoVibe Mobile (Fixed & Polished) */
(function() {
    let startY = 0;
    let isPulling = false;
    const threshold = 150;
    let refreshIndicator = null;
    
    function createIndicator() {
        if (refreshIndicator) return;
        refreshIndicator = document.createElement('div');
        refreshIndicator.id = 'ptr-indicator';
        refreshIndicator.innerHTML = `
            <div class="ptr-spinner"></div>
            <div class="ptr-text">Pull to refresh</div>
        `;
        document.body.prepend(refreshIndicator);
    }

    document.addEventListener('touchstart', (e) => {
        // Cek scroll di body atau di container utama yang scrollable
        const scrollTarget = document.querySelector('.center-area') || window;
        const scrollTop = (scrollTarget === window) ? window.scrollY : scrollTarget.scrollTop;
        
        if (scrollTop <= 1) {
            startY = e.touches[0].pageY;
            createIndicator();
        } else {
            startY = 0;
        }
    }, { passive: true });

    document.addEventListener('touchmove', (e) => {
        if (startY === 0) return;
        
        const y = e.touches[0].pageY;
        const pull = y - startY;
        
        if (pull > 0) {
            isPulling = true;
            const progress = Math.min(pull / threshold, 1.2);
            refreshIndicator.style.transform = `translateY(${pull * 0.4}px)`;
            refreshIndicator.style.opacity = progress;
            refreshIndicator.querySelector('.ptr-text').textContent = pull > threshold ? 'Release to refresh' : 'Pull to refresh';
            refreshIndicator.querySelector('.ptr-spinner').style.transform = `rotate(${pull * 2}px)`;
            
            // Prevent browser default pull-to-refresh if possible
            if (pull > 10 && e.cancelable) e.preventDefault();
        }
    }, { passive: false });

    document.addEventListener('touchend', (e) => {
        if (isPulling && refreshIndicator) {
            const pull = e.changedTouches[0].pageY - startY;
            if (pull > threshold) {
                refreshIndicator.classList.add('refreshing');
                refreshIndicator.querySelector('.ptr-text').textContent = 'Refreshing...';
                setTimeout(() => location.reload(), 500);
            } else {
                refreshIndicator.style.transform = 'translateY(-100%)';
                refreshIndicator.style.opacity = '0';
            }
        }
        startY = 0;
        isPulling = false;
    }, { passive: true });
})();
