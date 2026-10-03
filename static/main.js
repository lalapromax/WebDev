document.addEventListener("DOMContentLoaded", () => {
    console.log("Page loaded.");
});

function toggleMenu() {
    document.getElementById('navLinks').classList.toggle('active');
}

/* ---------------------------------------------------------------
   Floating interactive stickers (login page decoration)
   Inspired by ncase.me/crowds: cheap per-frame physics, cursor
   acts as a repeller. No item-to-item awareness on purpose —
   these are a few decorative props, not a crowd sim.
------------------------------------------------------------------ */
(function () {
    const container = document.getElementById('floatingItems');
    if (!container) return;

    const isCampaignPage = container.dataset.layout === 'campaign';
    const isContactPage = container.dataset.layout === 'contact';
    if (window.innerWidth <= 1000) return;
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;

    // Add / remove / reposition stickers here. x & y are % of viewport.
    const REGISTER_ITEM_CONFIG = [
        { src: '/static/assets/insulin.png', x: 30,  y: 25, size: '15vh', baseRot: 0 },
        { src: '/static/assets/bloodpressure.png', x: 15,  y: 25, size: '20vh', baseRot: 45 },
        { src: '/static/assets/oxygenbottle.png', x: 7,  y: 60, size: '20vh', baseRot: -20 },
        { src: '/static/assets/mask.png', x:25,  y: 50, size: '18vh', baseRot: 30 },
        { src: '/static/assets/pcr.png', x: 18,  y: 80, size: '18vh', baseRot: 15 },
        { src: '/static/assets/thermometer.png', x: 30,  y: 85, size: '20vh', baseRot: -67 },
        { src: '/static/assets/needle.png', x: 90, y: 23    , size: '15vh', baseRot: -45 },
        { src: '/static/assets/stethoscope.png', x: 73, y: 30, size: '14vh', baseRot: -45 },
        { src: '/static/assets/bed.png', x: 82, y: 50, size: '30vh', baseRot: 0 },
        { src: '/static/assets/ivfluids.png', x: 90, y: 73, size: '22vh', baseRot: -20 },
        { src: '/static/assets/wheelchair.png', x: 75, y: 81, size: '22vh', baseRot: 30 },
    ];
    const CAMPAIGN_ITEM_CONFIG = [
        { src: '/static/assets/insulin.png', x: 30,  y: 25, size: '15vh', baseRot: 0 },
        { src: '/static/assets/bloodpressure.png', x: 15,  y: 25, size: '20vh', baseRot: 45 },
        { src: '/static/assets/oxygenbottle.png', x: 7,  y: 60, size: '20vh', baseRot: -20 },
        { src: '/static/assets/mask.png', x:25,  y: 50, size: '18vh', baseRot: 30 },
        { src: '/static/assets/pcr.png', x: 18,  y: 80, size: '18vh', baseRot: 15 },
        { src: '/static/assets/thermometer.png', x: 30,  y: 85, size: '20vh', baseRot: -67 },
        { src: '/static/assets/needle.png', x: 90, y: 23    , size: '15vh', baseRot: -45 },
        { src: '/static/assets/stethoscope.png', x: 73, y: 30, size: '14vh', baseRot: -45 },
        { src: '/static/assets/bed.png', x: 82, y: 50, size: '30vh', baseRot: 0 },
        { src: '/static/assets/ivfluids.png', x: 90, y: 73, size: '22vh', baseRot: -20 },
        { src: '/static/assets/wheelchair.png', x: 75, y: 81, size: '22vh', baseRot: 30 },
    ];
    const CONTACT_ITEM_CONFIG = [
        { src: '/static/assets/insulin.png', x: 30,  y: 25, size: '15vh', baseRot: 0 },
        { src: '/static/assets/bloodpressure.png', x: 15,  y: 25, size: '20vh', baseRot: 45 },
        { src: '/static/assets/oxygenbottle.png', x: 7,  y: 60, size: '20vh', baseRot: -20 },
        { src: '/static/assets/mask.png', x:25,  y: 50, size: '18vh', baseRot: 30 },
        { src: '/static/assets/pcr.png', x: 18,  y: 80, size: '18vh', baseRot: 15 },
        { src: '/static/assets/thermometer.png', x: 30,  y: 85, size: '20vh', baseRot: -67 },
        { src: '/static/assets/needle.png', x: 90, y: 23    , size: '15vh', baseRot: -45 },
        { src: '/static/assets/stethoscope.png', x: 73, y: 30, size: '14vh', baseRot: -45 },
        { src: '/static/assets/bed.png', x: 82, y: 50, size: '30vh', baseRot: 0 },
        { src: '/static/assets/ivfluids.png', x: 90, y: 73, size: '22vh', baseRot: -20 },
        { src: '/static/assets/wheelchair.png', x: 75, y: 81, size: '22vh', baseRot: 30 },
    ];
    const itemConfig = isContactPage
        ? CONTACT_ITEM_CONFIG
        : isCampaignPage ? CAMPAIGN_ITEM_CONFIG : REGISTER_ITEM_CONFIG;

    const REPEL_RADIUS   = 1140;   // px, how close the cursor needs to get
    const REPEL_STRENGTH = 167;    // push force
    const FRICTION       = 0.94;   // velocity kept per frame
    const RESTITUTION    = 0.6;    // bounce energy kept off screen edges
    const HOME_PULL      = 0.004;  // gentle drift back to resting spot
    const THROW_BOOST    = 6.7;    // multiplies release velocity for a snappier throw

    let mouseX = -9999, mouseY = -9999, mouseActive = false;

    const items = itemConfig.map((cfg, i) => {
        const el = document.createElement('img');
        el.src = cfg.src;
        el.className = 'floating-item';
        el.alt = '';
        el.draggable = false;
        if (cfg.size) {
            el.style.width = cfg.size;
            el.style.height = cfg.size;
        }
        container.appendChild(el);

        const item = {
            el,
            homeXPct: cfg.x,
            homeYPct: cfg.y,
            baseRot: cfg.baseRot || 0,
            rot: cfg.baseRot || 0,
            x: (cfg.x / 100) * window.innerWidth,
            y: (cfg.y / 100) * window.innerHeight,
            vx: 0, vy: 0,
            width: el.offsetWidth || 90,
            height: el.offsetHeight || 90,
            dragging: false,
            grabDX: 0, grabDY: 0,
            lastPX: 0, lastPY: 0,
            bobPhase: i * 1.7,
            bobSpeed: 0.8 + (i % 3) * 0.15,
        };

        el.addEventListener('pointerdown', (e) => {
            item.dragging = true;
            item.el.classList.add('dragging');
            item.el.setPointerCapture(e.pointerId);
            item.grabDX = e.clientX - item.x;
            item.grabDY = e.clientY - item.y;
            item.lastPX = e.clientX;
            item.lastPY = e.clientY;
            item.vx = 0;
            item.vy = 0;
        });

        el.addEventListener('pointermove', (e) => {
            if (!item.dragging) return;
            item.x = e.clientX - item.grabDX;
            item.y = e.clientY - item.grabDY;
            item.vx = e.clientX - item.lastPX;
            item.vy = e.clientY - item.lastPY;
            item.lastPX = e.clientX;
            item.lastPY = e.clientY;
        });

        const release = () => {
            if (!item.dragging) return;
            item.dragging = false;
            item.el.classList.remove('dragging');
            item.vx *= THROW_BOOST;
            item.vy *= THROW_BOOST;
        };
        el.addEventListener('pointerup', release);
        el.addEventListener('pointercancel', release);

        return item;
    });

    document.addEventListener('pointermove', (e) => {
        mouseX = e.clientX;
        mouseY = e.clientY;
        mouseActive = true;
    });
    document.addEventListener('pointerleave', () => { mouseActive = false; });

    let resizeTimer;
    window.addEventListener('resize', () => {
        clearTimeout(resizeTimer);
        resizeTimer = setTimeout(() => {
            items.forEach((item) => {
                item.width = item.el.offsetWidth;
                item.height = item.el.offsetHeight;
            });
        }, 150);
    });

    function step() {
        const t = performance.now() / 1000;

        items.forEach((item) => {
            if (!item.dragging) {
                const homeX = (item.homeXPct / 100) * window.innerWidth;
                const homeY = (item.homeYPct / 100) * window.innerHeight;

                if (mouseActive) {
                    const dx = item.x - mouseX;
                    const dy = item.y - mouseY;
                    const dist = Math.sqrt(dx * dx + dy * dy) || 1;
                    if (dist < REPEL_RADIUS) {
                        const force = (1 - dist / REPEL_RADIUS) * REPEL_STRENGTH / dist;
                        item.vx += dx * force * 0.0016;
                        item.vy += dy * force * 0.0016;
                    }
                }

                item.vx += (homeX - item.x) * HOME_PULL;
                item.vy += (homeY - item.y) * HOME_PULL;

                item.x += item.vx;
                item.y += item.vy;
                item.vx *= FRICTION;
                item.vy *= FRICTION;

                const halfW = item.width / 2, halfH = item.height / 2;
                if (item.x < halfW) { item.x = halfW; item.vx *= -RESTITUTION; }
                if (item.x > window.innerWidth - halfW) { item.x = window.innerWidth - halfW; item.vx *= -RESTITUTION; }
                if (item.y < halfH) { item.y = halfH; item.vy *= -RESTITUTION; }
                if (item.y > window.innerHeight - halfH) { item.y = window.innerHeight - halfH; item.vy *= -RESTITUTION; }

                item.rot = item.baseRot + Math.max(-25, Math.min(25, item.vx * 1.4));
            }

            const bobX = item.dragging ? 0 : Math.sin(t * item.bobSpeed + item.bobPhase) * 3;
            const bobY = item.dragging ? 0 : Math.cos(t * item.bobSpeed * 0.8 + item.bobPhase) * 3;

            item.el.style.transform =
                `translate3d(${item.x - item.width / 2 + bobX}px, ${item.y - item.height / 2 + bobY}px, 0) rotate(${item.rot}deg)`;
        });

        requestAnimationFrame(step);
    }

    requestAnimationFrame(step);
})();