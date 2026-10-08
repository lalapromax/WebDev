document.addEventListener("DOMContentLoaded", () => {
    console.log("Page loaded.");

    const imageInputs = document.getElementById("coverPhotoInputs");
    if (!imageInputs) return;
    let savedImageCount = Number(imageInputs.dataset.savedImageCount || 0);
    let savedImageUrls = JSON.parse(imageInputs.dataset.savedImageUrls || "[]");
    const fundraiserForm = document.getElementById("fundraiserForm");
    const needsEquipmentCheckbox = document.getElementById("needsEquipment");
    const neededItemsFields = document.getElementById("neededItemsFields");
    const neededItemsInput = document.getElementById("neededItems");
    const addPhotoControl = document.getElementById("addPhotoControl");
    const formStatus = document.getElementById("formStatus");
    const youtubeInput = document.getElementById("youtubeLink");
    const preview = document.getElementById("coverPhotoPreview");
    const previewImage = document.getElementById("coverPhotoPreviewImage");
    const previewCount = document.getElementById("coverPhotoPreviewCount");
    const previousButton = document.getElementById("previousCoverPhoto");
    const nextButton = document.getElementById("nextCoverPhoto");

    const syncNeededItemsFields = () => {
        const enabled = needsEquipmentCheckbox.checked;
        neededItemsFields.hidden = !enabled;
        neededItemsInput.disabled = !enabled;
    };
    syncNeededItemsFields();
    needsEquipmentCheckbox.addEventListener("change", syncNeededItemsFields);

    const getRemovedImageIndices = () => new Set(
        Array.from(fundraiserForm.querySelectorAll('[data-remove-image-index][aria-pressed="true"]'))
            .map((input) => Number(input.dataset.removeImageIndex)),
    );
    const getRemainingSavedImageCount = () => savedImageCount - getRemovedImageIndices().size;
    const syncReplacementFilename = (input) => {
        const filename = input.files[0]?.name || input.dataset.currentFilename;
        const display = input.closest(".saved-photo-control").querySelector("[data-current-file-display]");
        display.textContent = filename;
        display.title = filename;
    };
    const syncImageInputLabels = () => {
        const inputs = Array.from(imageInputs.querySelectorAll('input[type="file"]'));
        inputs.forEach((input, index) => {
            const inputId = index === 0 ? "coverPhoto" : `coverPhoto${index}`;
            input.id = inputId;
            input.setAttribute("aria-label", `Cover photo ${index + 1}`);
            const row = input.closest(".cover-photo-input-row");
            row.querySelector(".photo-file-choose").htmlFor = inputId;
            row.querySelector("[data-upload-file-display]").textContent =
                input.files[0]?.name || "No file chosen";
            const removeButton = row
                .querySelector(".remove-photo-button");
            removeButton.setAttribute("aria-label", `Remove photo ${index + 1}`);
            removeButton.hidden = input.files.length === 0;
            const reorderHandle = row.querySelector(".photo-reorder-handle");
            reorderHandle.hidden = input.files.length === 0;
            reorderHandle.setAttribute(
                "aria-label",
                `Drag to reorder photo ${index + 1}. Use the arrow keys to move.`,
            );
        });
    };
    const syncSavedPhotoOrder = () => {
        Array.from(fundraiserForm.querySelectorAll(".saved-photo-row")).forEach((row, index) => {
            const imageInput = row.querySelector("[data-replace-image-index]");
            row.querySelector('[name="imageOrder"]').value = imageInput.dataset.replaceImageIndex;
            row.querySelector(".photo-reorder-handle").setAttribute(
                "aria-label",
                `Drag to reorder photo ${index + 1}. Use the arrow keys to move.`,
            );
        });
    };

    fundraiserForm.addEventListener("invalid", () => {
        const missingFields = [];
        const amountInput = document.getElementById("fundraiserAmount");
        const titleInput = document.getElementById("title");
        const hasImage = getRemainingSavedImageCount() > 0 || Array.from(imageInputs.querySelectorAll('input[type="file"]'))
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

    fundraiserForm.addEventListener("submit", async (event) => {
        event.preventDefault();

        const submitButton = fundraiserForm.querySelector('button[type="submit"]');
        submitButton.disabled = true;
        formStatus.textContent = "Saving fundraiser details...";

        try {
            const formData = new FormData(fundraiserForm);
            formData.delete("coverPhotos");
            Array.from(imageInputs.querySelectorAll('input[type="file"]'))
                .filter((input) => input.dataset.persisted !== "true")
                .forEach((input) => Array.from(input.files).forEach((file) => {
                    formData.append("coverPhotos", file);
                }));

            const response = await fetch(fundraiserForm.action, {
                method: "POST",
                body: formData,
                credentials: "same-origin",
            });

            if (response.redirected) {
                const previewMatch = new URL(response.url).pathname.match(/^\/campaign-preview\/([^/]+)$/);
                if (previewMatch) {
                    const campaignId = decodeURIComponent(previewMatch[1]);

                    let campaignIdInput = fundraiserForm.querySelector('input[name="campaignId"]');
                    if (!campaignIdInput) {
                        campaignIdInput = document.createElement("input");
                        campaignIdInput.type = "hidden";
                        campaignIdInput.name = "campaignId";
                        fundraiserForm.appendChild(campaignIdInput);
                    }
                    campaignIdInput.value = campaignId;

                    const formAction = new URL(fundraiserForm.action);
                    formAction.searchParams.set("campaign_id", campaignId);
                    fundraiserForm.action = formAction.toString();

                    const editorUrl = new URL(window.location.href);
                    editorUrl.searchParams.set("campaign_id", campaignId);
                    window.history.replaceState(window.history.state, "", editorUrl.toString());

                    const newlySavedInputs = Array.from(imageInputs.querySelectorAll('input[type="file"]'))
                        .filter((input) => input.files.length && input.dataset.persisted !== "true");

                    newlySavedInputs.forEach((input) => {
                        input.dataset.persisted = "true";
                    });
                    savedImageCount += newlySavedInputs.length;
                    imageInputs.dataset.savedImageCount = String(savedImageCount);
                    ensureTrailingImageInput();
                    syncImageRequirement();
                    refreshPreviews();
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
        previewUrls = [];
        const replacementInputs = Array.from(fundraiserForm.querySelectorAll("[data-replace-image-index]"));
        const removedImageIndices = getRemovedImageIndices();
        const savedRows = Array.from(fundraiserForm.querySelectorAll(".saved-photo-row"));
        previewItems = savedRows.flatMap((row) => {
            const index = Number(row.querySelector("[data-replace-image-index]").dataset.replaceImageIndex);
            const url = savedImageUrls[index];
            if (removedImageIndices.has(index)) return [];

            const replacementInput = replacementInputs.find(
                (input) => Number(input.dataset.replaceImageIndex) === index,
            );
            const replacementImage = replacementInput?.files[0];
            if (!replacementImage) return [{ src: url, alt: `Saved cover photo ${index + 1}` }];

            const replacementUrl = URL.createObjectURL(replacementImage);
            previewUrls.push(replacementUrl);
            return [{ src: replacementUrl, alt: replacementImage.name }];
        });
        selectedImages.forEach((image) => {
            const imageUrl = URL.createObjectURL(image);
            previewUrls.push(imageUrl);
            previewItems.push({ src: imageUrl, alt: image.name });
        });
        currentImage = previewItems.length
            ? Math.max(0, Math.min(currentImage, previewItems.length - 1))
            : 0;
        showCurrentImage();
    };

    const enablePhotoRowReordering = (container, rowSelector, isFilledRow, onReorder) => {
        if (!container) return;

        let draggedRow = null;
        let pointerId = null;
        let startX = 0;
        let startY = 0;
        let didMove = false;

        const stopDragging = (event) => {
            if (!draggedRow || event.pointerId !== pointerId) return;
            draggedRow.classList.remove("is-dragging");
            draggedRow = null;
            pointerId = null;
            document.removeEventListener("pointermove", moveRow);
            document.removeEventListener("pointerup", stopDragging);
            document.removeEventListener("pointercancel", stopDragging);
            if (didMove) onReorder();
            didMove = false;
        };

        const moveRow = (event) => {
            if (!draggedRow || event.pointerId !== pointerId) return;
            if (!draggedRow.classList.contains("is-dragging")) {
                if (Math.hypot(event.clientX - startX, event.clientY - startY) < 5) return;
                draggedRow.classList.add("is-dragging");
                didMove = true;
            }
            event.preventDefault();

            const rows = Array.from(container.querySelectorAll(rowSelector))
                .filter((row) => row !== draggedRow && isFilledRow(row));
            const nextRow = rows.find((row) => {
                const bounds = row.getBoundingClientRect();
                return event.clientY < bounds.top + bounds.height / 2;
            });
            const emptyRow = Array.from(container.querySelectorAll(rowSelector))
                .find((row) => !isFilledRow(row));
            container.insertBefore(draggedRow, nextRow || emptyRow || null);
        };

        container.addEventListener("pointerdown", (event) => {
            if (!(event.target instanceof Element)
                || !event.target.closest(".photo-reorder-handle")) return;
            const handle = event.target.closest(".photo-reorder-handle");
            if (handle.disabled || handle.hidden) return;
            const row = handle.closest(rowSelector);
            if (!row || !isFilledRow(row)) return;

            draggedRow = row;
            pointerId = event.pointerId;
            startX = event.clientX;
            startY = event.clientY;
            didMove = false;
            event.preventDefault();
            document.addEventListener("pointermove", moveRow);
            document.addEventListener("pointerup", stopDragging);
            document.addEventListener("pointercancel", stopDragging);
        });

        container.addEventListener("keydown", (event) => {
            if (!(event.target instanceof Element)
                || !event.target.matches(".photo-reorder-handle")
                || !["ArrowUp", "ArrowDown"].includes(event.key)) return;

            const row = event.target.closest(rowSelector);
            if (!row) return;
            const rows = Array.from(container.querySelectorAll(rowSelector)).filter(isFilledRow);
            const rowIndex = rows.indexOf(row);
            const targetRow = rows[rowIndex + (event.key === "ArrowUp" ? -1 : 1)];
            if (!targetRow) return;

            event.preventDefault();
            if (event.key === "ArrowUp") {
                container.insertBefore(row, targetRow);
            } else {
                container.insertBefore(targetRow, row);
            }
            onReorder();
            row.querySelector(".photo-reorder-handle").focus();
        });
    };

    window.addEventListener("pageshow", (event) => {
        if (event.persisted && new URL(window.location.href).searchParams.has("campaign_id")) {
            window.location.reload();
            return;
        }

        ensureTrailingImageInput();
        if (Array.from(imageInputs.querySelectorAll('input[type="file"]')).some((input) => input.files.length > 0)) {
            refreshPreviews();
        }
    });

    const ensureTrailingImageInput = () => {
        const inputs = Array.from(imageInputs.querySelectorAll('input[type="file"]'));
        const emptyInputs = inputs.filter((input) => input.files.length === 0);
        const savedPhotoRowCount = fundraiserForm.querySelectorAll("[data-replace-image-index]").length;
        const occupiedRowCount = savedPhotoRowCount + inputs.length - emptyInputs.length;
        if (occupiedRowCount >= 5) {
            emptyInputs.forEach((input) => input.closest(".cover-photo-input-row").remove());
            syncImageInputLabels();
            return;
        }

        emptyInputs.slice(1).forEach((input) => input.closest(".cover-photo-input-row").remove());
        if (emptyInputs.length) {
            syncImageInputLabels();
            return;
        }

        const nextInput = document.createElement("input");
        nextInput.type = "file";
        nextInput.name = "coverPhotos";
        nextInput.accept = "image/*";
        nextInput.className = "photo-file-input";
        const picker = document.createElement("div");
        picker.className = "photo-file-picker";
        picker.appendChild(nextInput);
        const chooseLabel = document.createElement("label");
        chooseLabel.className = "photo-file-choose";
        chooseLabel.textContent = "Choose File";
        picker.appendChild(chooseLabel);
        const filename = document.createElement("span");
        filename.className = "photo-file-filename";
        filename.dataset.uploadFileDisplay = "";
        filename.textContent = "No file chosen";
        picker.appendChild(filename);

        const row = document.createElement("div");
        row.className = "cover-photo-input-row";
        const reorderHandle = document.createElement("button");
        reorderHandle.className = "photo-reorder-handle";
        reorderHandle.type = "button";
        reorderHandle.textContent = "\u22ee";
        reorderHandle.title = "Drag to reorder";
        reorderHandle.hidden = true;
        row.appendChild(reorderHandle);
        row.appendChild(picker);

        const removeButton = document.createElement("button");
        removeButton.className = "remove-photo-button";
        removeButton.type = "button";
        removeButton.textContent = "Remove";
        removeButton.hidden = true;
        row.appendChild(removeButton);
        imageInputs.appendChild(row);
        syncImageInputLabels();
    };

    const syncImageRequirement = () => {
        const removedImageIndices = getRemovedImageIndices();
        const inputs = Array.from(imageInputs.querySelectorAll('input[type="file"]'));
        const savedPhotoRowCount = fundraiserForm.querySelectorAll("[data-replace-image-index]").length;
        addPhotoControl.hidden = savedPhotoRowCount >= 5 && inputs.length === 0;
        fundraiserForm.querySelectorAll("[data-replace-image-index]").forEach((input) => {
            const isRemoved = removedImageIndices.has(Number(input.dataset.replaceImageIndex));
            input.disabled = isRemoved;
            syncReplacementFilename(input);
        });

        inputs.forEach((input) => {
            if (!input.files.length) input.disabled = getRemainingSavedImageCount() >= 5;
        });
        const firstImageInput = inputs.find((input) => input.dataset.persisted !== "true") || inputs[0];
        if (firstImageInput) {
            firstImageInput.required = getRemainingSavedImageCount() === 0 && !youtubeInput.value.trim();
        }
    };

    syncImageRequirement();
    syncImageInputLabels();
    syncSavedPhotoOrder();
    enablePhotoRowReordering(
        imageInputs,
        ".cover-photo-input-row",
        (row) => row.querySelector('input[type="file"]').files.length > 0,
        () => {
            syncImageInputLabels();
            refreshPreviews();
        },
    );
    enablePhotoRowReordering(
        fundraiserForm.querySelector(".saved-photo-replacements"),
        ".saved-photo-row",
        () => true,
        () => {
            syncSavedPhotoOrder();
            refreshPreviews();
        },
    );
    youtubeInput.addEventListener("input", syncImageRequirement);
    fundraiserForm.querySelectorAll("[data-replace-image-index]").forEach((input) => {
        input.addEventListener("change", () => {
            currentImage = Number(input.dataset.replaceImageIndex);
            syncReplacementFilename(input);
            refreshPreviews();
        });
    });
    fundraiserForm.querySelectorAll("[data-remove-image-index]").forEach((button) => {
        button.addEventListener("click", () => {
            const isRemoved = button.getAttribute("aria-pressed") === "true";
            if (isRemoved) {
                const newPhotoCount = Array.from(imageInputs.querySelectorAll('input[type="file"]'))
                    .filter((fileInput) => fileInput.dataset.persisted !== "true")
                    .reduce((count, fileInput) => count + fileInput.files.length, 0);
                if (getRemainingSavedImageCount() + 1 + newPhotoCount > 5) {
                    formStatus.textContent = "Remove an added photo before restoring this saved photo.";
                    return;
                }
            }

            const removing = !isRemoved;
            button.setAttribute("aria-pressed", String(removing));
            button.textContent = removing ? "Undo" : "Remove";
            document.getElementById(button.dataset.removeFieldId).value = removing ? "1" : "0";
            syncImageRequirement();
            ensureTrailingImageInput();
            currentImage = 0;
            refreshPreviews();
        });
    });

    imageInputs.addEventListener("change", (event) => {
        const changedInput = event.target;
        if (!(changedInput instanceof HTMLInputElement) || changedInput.type !== "file") return;
        if (changedInput.dataset.persisted === "true") {
            changedInput.dataset.persisted = "false";
        }

        const inputs = Array.from(imageInputs.querySelectorAll('input[type="file"]'));
        const changedIndex = inputs.indexOf(changedInput);
        if (!changedInput.files.length) {
            inputs.slice(changedIndex + 1).forEach((input) => {
                input.closest(".cover-photo-input-row").remove();
            });
        }

        ensureTrailingImageInput();
        syncImageInputLabels();
        syncImageRequirement();
        refreshPreviews();
    });

    imageInputs.addEventListener("click", (event) => {
        const target = event.target;
        if (!(target instanceof Element)) return;
        const removeButton = target.closest(".cover-photo-input-row .remove-photo-button");
        if (!removeButton) return;

        removeButton.closest(".cover-photo-input-row").remove();
        ensureTrailingImageInput();
        syncImageInputLabels();
        syncImageRequirement();
        refreshPreviews();
    });

    previousButton.addEventListener("click", () => {
        currentImage = (currentImage - 1 + previewItems.length) % previewItems.length;
        showCurrentImage();
    });

    nextButton.addEventListener("click", () => {
        currentImage = (currentImage + 1) % previewItems.length;
        showCurrentImage();
    });

    refreshPreviews();
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