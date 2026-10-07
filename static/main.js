document.addEventListener("DOMContentLoaded", () => {
    console.log("Page loaded.");

    const imageInputs = document.getElementById("coverPhotoInputs");
    if (!imageInputs) return;
    let savedImageCount = Number(imageInputs.dataset.savedImageCount || 0);
    let savedImageUrls = JSON.parse(imageInputs.dataset.savedImageUrls || "[]");
    const fundraiserForm = document.getElementById("fundraiserForm");
    const formStatus = document.getElementById("formStatus");
    const youtubeInput = document.getElementById("youtubeLink");
    const savedImages = document.getElementById("savedImages");
    const savedImageList = document.getElementById("savedImageList");
    const preview = document.getElementById("coverPhotoPreview");
    const previewImage = document.getElementById("coverPhotoPreviewImage");
    const previewCount = document.getElementById("coverPhotoPreviewCount");
    const previousButton = document.getElementById("previousCoverPhoto");
    const nextButton = document.getElementById("nextCoverPhoto");

    fundraiserForm.addEventListener("invalid", () => {
        const missingFields = [];
        const amountInput = document.getElementById("fundraiserAmount");
        const titleInput = document.getElementById("title");
        const hasImage = savedImageCount > 0 || Array.from(imageInputs.querySelectorAll('input[type="file"]'))
            .some((input) => input.files.length > 0);

        if (!amountInput.validity.valid) missingFields.push("fundraiser amount");
        if (!titleInput.validity.valid) missingFields.push("title");
        if (!youtubeInput.value.trim() && !hasImage) {
            missingFields.push("at least one cover image or YouTube link");
        } else if (youtubeInput.value.trim() && !youtubeInput.validity.valid) {
            missingFields.push("a valid YouTube link");
        }

        formStatus.textContent = `Complete these before saving: ${missingFields.join(", ")}.`;
    }, true);

    fundraiserForm.addEventListener("input", () => {
        if (fundraiserForm.checkValidity()) formStatus.textContent = "";
    });

    window.addEventListener("pageshow", () => {
        const campaignId = window.sessionStorage.getItem("campaignIdForBackForm");
        if (window.location.pathname !== "/campaign-create" || !campaignId) return;

        let campaignIdInput = fundraiserForm.querySelector('input[name="campaignId"]');
        const isNewForm = !campaignIdInput;
        if (!campaignIdInput) {
            campaignIdInput = document.createElement("input");
            campaignIdInput.type = "hidden";
            campaignIdInput.name = "campaignId";
            fundraiserForm.appendChild(campaignIdInput);
        }
        campaignIdInput.value = campaignId;

        let restoredImageUrls = [];
        try {
            restoredImageUrls = JSON.parse(window.sessionStorage.getItem("campaignBackImages") || "[]");
        } catch {
            restoredImageUrls = [];
        }

        if (restoredImageUrls.length) {
            savedImageUrls = restoredImageUrls;
            savedImageCount = savedImageUrls.length;
            imageInputs.dataset.savedImageCount = String(savedImageCount);
            imageInputs.dataset.savedImageUrls = JSON.stringify(savedImageUrls);
            savedImageList.replaceChildren(...savedImageUrls.map((imageUrl, index) => {
                const image = document.createElement("img");
                image.src = imageUrl;
                image.alt = `Saved cover photo ${index + 1}`;
                return image;
            }));
            savedImages.hidden = false;
        }

        const fileInputs = Array.from(imageInputs.querySelectorAll('input[type="file"]'));
        fileInputs.slice(1).forEach((input) => input.remove());
        if (fileInputs[0]) {
            fileInputs[0].value = "";
            fileInputs[0].disabled = savedImageCount >= 5;
        }
        fundraiserForm.querySelector('input[name="replaceAllImages"]').value = "0";
        syncImageRequirement();
        refreshPreviews();

        window.sessionStorage.removeItem("campaignIdForBackForm");
        window.sessionStorage.removeItem("campaignBackImages");
    });

    fundraiserForm.addEventListener("submit", async (event) => {
        event.preventDefault();

        const submitButton = fundraiserForm.querySelector('button[type="submit"]');
        submitButton.disabled = true;
        formStatus.textContent = "Saving fundraiser details...";

        try {
            const response = await fetch(fundraiserForm.action, {
                method: "POST",
                body: new FormData(fundraiserForm),
                credentials: "same-origin",
            });

            if (response.redirected) {
                const previewMatch = new URL(response.url).pathname.match(/^\/campaign-preview\/([^/]+)$/);
                if (previewMatch) {
                    window.sessionStorage.setItem(
                        "campaignPreviewBackId",
                        decodeURIComponent(previewMatch[1]),
                    );
                }
                window.location.assign(response.url);
                return;
            }

            const responseDocument = new DOMParser().parseFromString(
                await response.text(),
                "text/html",
            );
            const serverError = responseDocument.querySelector('[role="alert"]');

            if (serverError) {
                formStatus.textContent = serverError.textContent.trim();
            } else if (!response.ok) {
                formStatus.textContent = `Could not save fundraiser details (HTTP ${response.status}). Your entries are still here; please try again.`;
            } else {
                formStatus.textContent = "Could not save fundraiser details. Your entries are still here; please try again.";
            }
        } catch {
            formStatus.textContent = "Could not reach the server. Your entries are still here; please try again.";
        } finally {
            submitButton.disabled = false;
        }
    });

    let selectedImages = [];
    let previewUrls = [];
    let previewItems = [];
    let currentImage = 0;

    const showCurrentImage = () => {
        if (!previewItems.length) {
            preview.hidden = true;
            previewImage.removeAttribute("src");
            previewCount.textContent = "";
            return;
        }

        preview.hidden = false;
    previewImage.src = previewItems[currentImage].src;
    previewImage.alt = previewItems[currentImage].alt;
    previewCount.textContent = `Image ${currentImage + 1} of ${previewItems.length}`;
    previousButton.disabled = previewItems.length < 2;
    nextButton.disabled = previewItems.length < 2;
    };

    const refreshPreviews = () => {
        previewUrls.forEach((url) => URL.revokeObjectURL(url));
        selectedImages = Array.from(imageInputs.querySelectorAll('input[type="file"]'))
            .flatMap((input) => Array.from(input.files));
        previewUrls = selectedImages.map((image) => URL.createObjectURL(image));
        previewItems = [
            ...savedImageUrls.map((url, index) => ({ src: url, alt: `Saved cover photo ${index + 1}` })),
            ...selectedImages.map((image, index) => ({ src: previewUrls[index], alt: image.name })),
        ];
        currentImage = Math.min(currentImage, previewItems.length - 1);
        showCurrentImage();
    };

    window.addEventListener("pageshow", () => {
        if (Array.from(imageInputs.querySelectorAll('input[type="file"]'))
            .some((input) => input.files.length > 0)) {
            refreshPreviews();
        }
    });

    const syncImageRequirement = () => {
        const firstImageInput = imageInputs.querySelector('input[type="file"]');
        if (firstImageInput) {
            firstImageInput.required = savedImageCount === 0 && !youtubeInput.value.trim();
        }
    };

    syncImageRequirement();
    youtubeInput.addEventListener("input", syncImageRequirement);

    imageInputs.addEventListener("change", (event) => {
        const changedInput = event.target;
        if (!(changedInput instanceof HTMLInputElement) || changedInput.type !== "file") return;

        const inputs = Array.from(imageInputs.querySelectorAll('input[type="file"]'));
        const changedIndex = inputs.indexOf(changedInput);
        if (changedInput.files.length && changedIndex === inputs.length - 1 && savedImageCount + inputs.length < 5) {
            const nextInput = document.createElement("input");
            nextInput.type = "file";
            nextInput.name = "coverPhotos";
            nextInput.accept = "image/*";
            nextInput.setAttribute("aria-label", `Cover photo ${inputs.length + 1}`);
            imageInputs.appendChild(nextInput);
        }

        if (!changedInput.files.length) {
            inputs.slice(changedIndex + 1).forEach((input) => input.remove());
        }

        syncImageRequirement();
        refreshPreviews();
    });

    previousButton.addEventListener("click", () => {
        currentImage = (currentImage - 1 + selectedImages.length) % selectedImages.length;
        showCurrentImage();
    });

    nextButton.addEventListener("click", () => {
        currentImage = (currentImage + 1) % selectedImages.length;
        showCurrentImage();
    });
});
/*Image upload Max File Size Validation*/
document.addEventListener("DOMContentLoaded", () => {
    const gallery = document.getElementById("campaignGallery");
    if (gallery) {
        const goalProgress = gallery.querySelector(".goal-progress");
        if (goalProgress) {
            const fill = goalProgress.querySelector(".goal-progress-fill");
            const percentage = Math.max(0, Math.min(100, Number(goalProgress.dataset.progress) || 0));
            fill.style.width = `${percentage}%`;
        }

        const slides = Array.from(gallery.querySelectorAll(".campaign-slide"));
        const count = document.getElementById("campaignImageCount");
        let currentSlide = 0;

        const showSlide = (index) => {
            currentSlide = (index + slides.length) % slides.length;
            slides.forEach((slide, slideIndex) => {
                const isActive = slideIndex === currentSlide;
                slide.hidden = !isActive;

                const video = slide.querySelector("iframe");
                if (!video) return;

                video.src = isActive ? video.dataset.src : "about:blank";
            });
            count.textContent = `${currentSlide + 1} of ${slides.length}`;
        };

        showSlide(0);

        document.getElementById("previousCampaignImage")?.addEventListener("click", () => {
            showSlide(currentSlide - 1);
        });
        document.getElementById("nextCampaignImage")?.addEventListener("click", () => {
            showSlide(currentSlide + 1);
        });
    }

    const shareButton = document.getElementById("shareCampaign");
    const shareStatus = document.getElementById("shareStatus");
    const editCampaignLink = document.getElementById("editCampaignLink");

    if (editCampaignLink) {
        const campaignId = window.location.pathname.split("/").pop();
        if (window.sessionStorage.getItem("campaignPreviewBackId") === campaignId) {
            editCampaignLink.addEventListener("click", (event) => {
                event.preventDefault();
                try {
                    const savedImageUrls = Array.from(
                        document.querySelectorAll("#campaignGallery img.campaign-slide"),
                    ).map((image) => image.getAttribute("src"));
                    window.sessionStorage.setItem("campaignBackImages", JSON.stringify(savedImageUrls));
                    window.sessionStorage.setItem(
                        "campaignIdForBackForm",
                        campaignId,
                    );
                    window.sessionStorage.removeItem("campaignPreviewBackId");
                    window.history.back();
                } catch {
                    window.location.assign(editCampaignLink.href);
                }
            });
        }
    }

    shareButton?.addEventListener("click", async () => {
        try {
            await navigator.clipboard.writeText(window.location.href);
            shareStatus.textContent = "Fundraiser link copied.";
        } catch {
            shareStatus.textContent = window.location.href;
        }
    });
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
    //Campaign-Create
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

document.getElementById('profilePicInput').addEventListener('change', function () {
    const file = this.files[0];
    if (!file) return;

    if (file.size > 5 * 1024 * 1024) {
        alert('Image must be under 5 MB.');
        this.value = '';
        return;
    }
    document.getElementById('profilePicPreview').src = URL.createObjectURL(file);
});