document.addEventListener("DOMContentLoaded", () => {
    console.log("Page loaded.");
});

function toggleMenu() {
    document.getElementById('navLinks').classList.toggle('active');
}

(function () {
    // Only run this scroll-transition logic on the homepage
    const scrollArrow = document.getElementById('scrollArrow');
    if (!scrollArrow) return; // element doesn't exist = not on index.html, so stop here

    const nextPageUrl = "/hook-page"; // adjust to your actual Flask route
    let isTransitioning = false;
    let scrollAccumulator = 0;
    const scrollThreshold = 150;

    function goToNextPage() {
        if (isTransitioning) return;
        isTransitioning = true;
        document.body.classList.add('page-exit');
        setTimeout(() => {
            window.location.href = nextPageUrl;
        }, 600);
    }

    window.addEventListener('wheel', function (e) {
        if (isTransitioning) return;
        if (e.deltaY > 0) {
            scrollAccumulator += e.deltaY;
            if (scrollAccumulator > scrollThreshold) {
                goToNextPage();
            }
        }
    }, { passive: true });

    let touchStartY = 0;
    window.addEventListener('touchstart', function (e) {
        touchStartY = e.touches[0].clientY;
    }, { passive: true });

    window.addEventListener('touchmove', function (e) {
        if (isTransitioning) return;
        const delta = touchStartY - e.touches[0].clientY;
        if (delta > scrollThreshold) {
            goToNextPage();
        }
    }, { passive: true });

    scrollArrow.addEventListener('click', goToNextPage);
})();

// const imageContainer = document.querySelector('.image-container');
//
// document.addEventListener('mousemove', (event) => {
//     const x = (event.clientX / window.innerWidth - 0.5) * 30;
//     const y = (event.clientY / window.innerHeight - 0.5) * 30;
//
//     imageContainer.style.setProperty('--translateX', `${-x}px`);
//     imageContainer.style.setProperty('--translateY', `${-y}px`);
// });
