function showPopup(message) {
    const popup = document.getElementById("flashPopup");
    const popupMessage = document.getElementById("popupMessage");
    const okayButton = document.getElementById("popupOkay");

    popupMessage.textContent = message;
    popup.hidden = false;

    okayButton.focus();
}

function closePopup() {
    document.getElementById("flashPopup").hidden = true;
}

function openPasswordModal() {
    const modal = document.getElementById("passwordModal");

    if (!modal) return;

    modal.hidden = false;

    const oldPassword = document.getElementById("oldPassword");
    if (oldPassword) oldPassword.focus();
}

function closePasswordModal() {
    const modal = document.getElementById("passwordModal");
    const form = document.getElementById("passwordResetForm");
    const status = document.getElementById("passwordResetError");

    if (!modal) return;

    modal.hidden = true;

    if (form) form.reset();

    if (status) {
        status.textContent = "";
        status.className = "password-reset-status";
        status.hidden = true;
    }
}

/* Close the modal when clicking the dark background */
document.addEventListener("click", function (event) {
    const modal = document.getElementById("passwordModal");

    if (modal && event.target === modal) {
        closePasswordModal();
    }
});

/* Close the modal using the Escape key */
document.addEventListener("keydown", function (event) {
    const modal = document.getElementById("passwordModal");

    if (event.key === "Escape" && modal && !modal.hidden) {
        closePasswordModal();
    }
});

/* Submit without navigating away from edit-profile */
document.addEventListener("submit", async function (event) {
    const form = event.target;

    if (!form || form.id !== "passwordResetForm") return;

    event.preventDefault();

    const status = document.getElementById("passwordResetError");
    const submitButton = document.getElementById("passwordSubmitBtn");

    const newPassword = form.elements["new_password"].value;
    const confirmPassword = form.elements["confirm_password"].value;

    status.hidden = true;
    status.textContent = "";

    if (newPassword !== confirmPassword) {
        status.textContent = "The new passwords do not match.";
        status.className = "password-reset-status error";
        status.hidden = false;
        return;
    }

    submitButton.disabled = true;
    submitButton.textContent = "Saving...";

    try {
        // getAttribute: a form control named "action" would hide the form.action property
        const response = await fetch(form.getAttribute("action"), {
            method: "POST",
            body: new FormData(form),
            headers: {
                "X-Requested-With": "XMLHttpRequest"
            }
        });

        // an error page instead of JSON means the server failed, not that the connection dropped
        const result = await response.json().catch(() => ({}));

        if (!response.ok || !result.success) {
            status.textContent =
                result.message || "Unable to update your password.";
            status.className = "password-reset-status error";
            status.hidden = false;
            return;
        }

        closePasswordModal();

        /* Use your existing custom popup in base.html */
        if (typeof showPopup === "function") {
            showPopup(result.message);
        }

    } catch (error) {
        status.textContent =
            "A connection error occurred. Please try again.";
        status.className = "password-reset-status error";
        status.hidden = false;

    } finally {
        submitButton.disabled = false;
        submitButton.textContent = "Save Password";
    }
});

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

    // Create-campaign has no stickers any more: an NGO is at work there, not browsing.
    if (container.dataset.layout === 'campaign') return;
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
    const itemConfig = isContactPage ? CONTACT_ITEM_CONFIG : REGISTER_ITEM_CONFIG;

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

function previewImage(inputId, imgId) {
    const input = document.getElementById(inputId);
    const img = document.getElementById(imgId);
    if (!input || !img) return;   // not on this page, so skip

    input.addEventListener('change', function () {
        const file = this.files[0];
        if (!file) return;

        if (file.size > 5 * 1024 * 1024) {
            alert('Image must be under 5 MB.');
            this.value = '';
            return;
        }

        img.src = URL.createObjectURL(file);
    });
}

previewImage('profilePicInput', 'profilePicPreview');
previewImage('coverInput', 'coverPreview');

function formatMoney(amount) {
    const n = Number(amount);
    if (!Number.isFinite(n)) return "RM 0.00";

    const abs = Math.abs(n);

    if (abs >= 1000000000000) return `RM ${Math.floor(n / 1000000000000)}T`;
    if (abs >= 1000000000)  return `RM ${Math.floor(n / 1000000000)}B`;
    if (abs >= 1000000)  return `RM ${Math.floor(n / 1000000)}M`;
    if (abs >= 1000)  return `RM ${Math.floor(n / 1000)}K`;

    return `RM ${n.toFixed(2)}`;
}

document.addEventListener("DOMContentLoaded", () => {
    document.querySelectorAll("[data-amount]").forEach((el) => {
        el.textContent = formatMoney(el.dataset.amount);
    });
});

document.addEventListener("DOMContentLoaded", () => {
    // Progress bars grow from 0 to their real width
    document.querySelectorAll("[data-width]").forEach((bar) => {
        bar.getBoundingClientRect(); // forces layout so the transition has a start point
        bar.style.width = `${Math.max(0, Math.min(100, Number(bar.dataset.width) || 0))}%`;
    });

    const dataTag = document.getElementById("ngoAnalyticsData");
    if (!dataTag || typeof Chart === "undefined") return;

    const data = JSON.parse(dataTag.textContent);
    // Colours come from the theme tokens in style.css, so an NGO's charts are green
    // and a donor's stay teal without a second set of numbers to keep in step.
    const theme = getComputedStyle(document.body);
    const token = (name, fallback) => theme.getPropertyValue(name).trim() || fallback;
    const rgb = (hex) => {
        const h = hex.replace("#", "");
        const full = h.length === 3 ? h.split("").map((c) => c + c).join("") : h;
        return [0, 2, 4].map((i) => parseInt(full.slice(i, i + 2), 16));
    };
    const alpha = (hex, a) => `rgba(${rgb(hex).join(", ")}, ${a})`;
    const tint = (hex, amount) => {            // mix a colour with white
        const [r, g, b] = rgb(hex);
        const lift = (c) => Math.round(c + (255 - c) * amount);
        return `rgb(${lift(r)}, ${lift(g)}, ${lift(b)})`;
    };
    const colors = {
        accent: token("--accent", "#327589"),
        mid: token("--accent-mid", "#5b9db0"),
        navy: token("--ink", "#162961"),
        soft: token("--accent-light", "#d9f4fc"),
    };
    colors.grid = alpha(colors.accent, 0.12);
    const compact = new Intl.NumberFormat("en", { notation: "compact" });
    const money = (value) => "RM " + Number(value).toLocaleString("en-MY", {
        minimumFractionDigits: 2,
        maximumFractionDigits: 2,
    });

    Chart.defaults.font.family = '"Inter", sans-serif';
    Chart.defaults.color = "#545454";
    Chart.defaults.plugins.tooltip.backgroundColor = colors.navy;
    Chart.defaults.plugins.tooltip.cornerRadius = 8;
    Chart.defaults.plugins.tooltip.padding = 10;
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
        Chart.defaults.animation = false;
    }

    // Funds raised: running total line, filtered by date (like Google's currency chart)
    const fundsCanvas = document.getElementById("fundsChart");
    if (fundsCanvas && Array.isArray(data.daily) && data.daily.length) {
        const DAY = 86400000;
        const toTime = (iso) => Date.parse(iso + "T00:00:00Z");
        const toIso = (time) => new Date(time).toISOString().slice(0, 10);
        const longDate = (time) => new Date(time).toLocaleDateString("en-MY", {
            weekday: "short", day: "numeric", month: "short", year: "numeric", timeZone: "UTC",
        });

        const perDay = new Map(data.daily.map(([day, rm]) => [toTime(day), rm]));
        const firstDay = Math.min(...perDay.keys());
        const lastDay = toTime(data.today);

        const totalEl = document.getElementById("fundsTotal");
        const changeEl = document.getElementById("fundsChange");
        const fromInput = document.getElementById("fundsFrom");
        const toInput = document.getElementById("fundsTo");
        const tabs = Array.from(document.querySelectorAll(".ngo-range-tab"));

        fromInput.min = toInput.min = toIso(firstDay);
        fromInput.max = toInput.max = toIso(lastDay);

        let series = { days: [], totals: [], daily: [] };
        const sumBetween = (from, to) => {
            let sum = 0;
            perDay.forEach((rm, time) => { if (time >= from && time <= to) sum += rm; });
            return sum;
        };

        const crosshair = {
            id: "ngoCrosshair",
            afterDatasetsDraw(chart) {
                const active = chart.tooltip && chart.tooltip.getActiveElements();
                if (!active || !active.length) return;
                const { ctx, chartArea } = chart;
                ctx.save();
                ctx.beginPath();
                ctx.moveTo(active[0].element.x, chartArea.top);
                ctx.lineTo(active[0].element.x, chartArea.bottom);
                ctx.lineWidth = 1;
                ctx.setLineDash([4, 4]);
                ctx.strokeStyle = alpha(colors.navy, 0.3);
                ctx.stroke();
                ctx.restore();
            },
        };

        const fundsChart = new Chart(fundsCanvas, {
            type: "line",
            data: {
                labels: [],
                datasets: [{
                    data: [],
                    borderColor: colors.accent,
                    borderWidth: 2.5,
                    cubicInterpolationMode: "monotone",
                    fill: true,
                    backgroundColor: (context) => {
                        const { ctx, chartArea } = context.chart;
                        if (!chartArea) return "transparent";
                        const gradient = ctx.createLinearGradient(0, chartArea.top, 0, chartArea.bottom);
                        gradient.addColorStop(0, alpha(colors.accent, 0.28));
                        gradient.addColorStop(1, alpha(colors.accent, 0));
                        return gradient;
                    },
                    pointRadius: 0,
                    pointHoverRadius: 5,
                    pointHoverBackgroundColor: colors.navy,
                    pointHoverBorderColor: "#fff",
                    pointHoverBorderWidth: 2,
                }],
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                interaction: { mode: "index", intersect: false },
                plugins: {
                    legend: { display: false },
                    tooltip: {
                        displayColors: false,
                        callbacks: {
                            title: (items) => longDate(series.days[items[0].dataIndex]),
                            label: (ctx) => `Total ${money(ctx.parsed.y)}`,
                            afterLabel: (ctx) => {
                                const that = series.daily[ctx.dataIndex];
                                return that > 0 ? `+${money(that)} that day` : "";
                            },
                        },
                    },
                },
                scales: {
                    x: {
                        grid: { display: false },
                        ticks: {
                            maxTicksLimit: 6,
                            maxRotation: 0,
                            callback(value) {
                                const time = series.days[value];
                                if (time === undefined) return "";
                                const longRange = series.days.length > 200;
                                return new Date(time).toLocaleDateString("en-MY", longRange
                                    ? { month: "short", year: "2-digit", timeZone: "UTC" }
                                    : { day: "numeric", month: "short", timeZone: "UTC" });
                            },
                        },
                    },
                    y: {
                        beginAtZero: true,
                        grid: { color: colors.grid },
                        ticks: { callback: (value) => `RM ${compact.format(value)}` },
                    },
                },
            },
            plugins: [crosshair],
        });

        function draw(from, to, label) {
            from = Math.max(firstDay, Math.min(from, lastDay));
            to = Math.max(from, Math.min(to, lastDay));

            series = { days: [], totals: [], daily: [] };
            let running = 0;
            for (let time = from; time <= to; time += DAY) {
                const that = perDay.get(time) || 0;
                running += that;
                series.days.push(time);
                series.daily.push(that);
                series.totals.push(Math.round(running * 100) / 100);
            }

            fundsChart.data.labels = series.days.map(toIso);
            fundsChart.data.datasets[0].data = series.totals;
            fundsChart.data.datasets[0].pointRadius = series.days.length <= 31 ? 3 : 0;
            fundsChart.update();

            fromInput.value = toIso(from);
            toInput.value = toIso(to);
            totalEl.textContent = money(running);

            // Compare with the period of the same length just before this one
            const length = to - from + DAY;
            const before = sumBetween(from - length, from - DAY);
            const showChange = label !== "all" && before > 0 && from - length >= firstDay;
            changeEl.hidden = !showChange;
            if (showChange) {
                const percent = ((running - before) / before) * 100;
                const days = Math.round(length / DAY);
                changeEl.textContent = `${percent >= 0 ? "▲" : "▼"} ${Math.abs(percent).toFixed(1)}% vs previous ${days} day${days === 1 ? "" : "s"}`;
                changeEl.classList.toggle("is-up", percent >= 0);
                changeEl.classList.toggle("is-down", percent < 0);
            }
        }

        function pickRange(range) {
            tabs.forEach((tab) => tab.setAttribute("aria-pressed", String(tab.dataset.range === range)));
            if (range === "all") return draw(firstDay, lastDay, "all");
            draw(lastDay - (Number(range) - 1) * DAY, lastDay, range);
        }

        tabs.forEach((tab) => tab.addEventListener("click", () => pickRange(tab.dataset.range)));

        // Typing/choosing dates by hand clears the quick tabs
        function pickDates() {
            if (!fromInput.value || !toInput.value) return;
            tabs.forEach((tab) => tab.setAttribute("aria-pressed", "false"));
            draw(toTime(fromInput.value), toTime(toInput.value), "custom");
        }
        fromInput.addEventListener("change", pickDates);
        toInput.addEventListener("change", pickDates);

        const startTab = tabs.find((tab) => tab.getAttribute("aria-pressed") === "true");
        pickRange(startTab ? startTab.dataset.range : "180");
    }

    // Goal progress (the % in the middle is plain HTML, see .ngo-donut-center)
    const goalCanvas = document.getElementById("goalChart");
    if (goalCanvas) {
        new Chart(goalCanvas, {
            type: "doughnut",
            data: {
                labels: ["Raised", "Remaining"],
                datasets: [{
                    data: [data.raised, data.remaining],
                    backgroundColor: [colors.accent, colors.soft],
                    borderWidth: 0,
                }],
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                cutout: "74%",
                plugins: {
                    legend: { display: false },
                    tooltip: { callbacks: { label: (ctx) => ` ${ctx.label}: ${money(ctx.parsed)}` } },
                },
            },
        });
    }

    // Donors by age group (the total in the middle is plain HTML, see .ngo-donut-center)
    const ageCanvas = document.getElementById("ageChart");
    if (ageCanvas && data.age) {
        const palette = [colors.navy, colors.accent, colors.mid, tint(colors.accent, 0.55), "#e3b04b", tint(colors.navy, 0.65)];
        const groups = data.age.labels
            .map((label, i) => ({ label, count: data.age.values[i], color: palette[i % palette.length] }))
            .filter((group) => group.count > 0);
        const total = groups.reduce((sum, group) => sum + group.count, 0);

        new Chart(ageCanvas, {
            type: "doughnut",
            data: {
                labels: groups.map((group) => group.label),
                datasets: [{
                    data: groups.map((group) => group.count),
                    backgroundColor: groups.map((group) => group.color),
                    borderWidth: 2,
                    borderColor: "#fff",
                }],
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                cutout: "68%",
                plugins: {
                    legend: { display: false },
                    tooltip: {
                        callbacks: {
                            label: (ctx) => ` ${ctx.label}: ${ctx.parsed} donor${ctx.parsed === 1 ? "" : "s"}`,
                        },
                    },
                },
            },
        });

        const legend = document.getElementById("ageLegend");
        if (legend) {
            groups.forEach((group) => {
                const item = document.createElement("li");
                const dot = document.createElement("span");
                dot.className = "ngo-dot";
                dot.style.background = group.color;
                const name = document.createElement("span");
                name.textContent = group.label;
                const count = document.createElement("b");
                count.textContent = `${group.count} · ${Math.round((group.count / total) * 100)}%`;
                item.append(dot, name, count);
                legend.append(item);
            });
        }
    }

    // Donors by state (counts are written at the end of each bar)
    const stateCanvas = document.getElementById("stateChart");
    if (stateCanvas && data.states) {
        const barLabels = {
            id: "ngoBarLabels",
            afterDatasetsDraw(chart) {
                const { ctx } = chart;
                ctx.save();
                ctx.font = '600 12px "Inter", sans-serif';
                ctx.fillStyle = colors.navy;
                ctx.textAlign = "left";
                ctx.textBaseline = "middle";
                chart.getDatasetMeta(0).data.forEach((bar, i) => {
                    ctx.fillText(chart.data.datasets[0].data[i], bar.x + 8, bar.y);
                });
                ctx.restore();
            },
        };

        new Chart(stateCanvas, {
            type: "bar",
            data: {
                labels: data.states.labels,
                datasets: [{
                    data: data.states.values,
                    backgroundColor: colors.accent,
                    hoverBackgroundColor: colors.navy,
                    borderRadius: 6,
                    maxBarThickness: 22,
                }],
            },
            options: {
                indexAxis: "y",
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: { display: false },
                    tooltip: {
                        callbacks: {
                            label: (ctx) => ` ${ctx.parsed.x} donor${ctx.parsed.x === 1 ? "" : "s"}`,
                        },
                    },
                },
                scales: {
                    x: { display: false, beginAtZero: true, grace: "15%" },
                    y: { grid: { display: false }, border: { display: false }, ticks: { autoSkip: false } },
                },
            },
            plugins: [barLabels],
        });
    }
});

/*Campaign Create*/
document.addEventListener("DOMContentLoaded", () => {
    const list = document.getElementById("neededItemRows");
    if (!list) return;

    const MAX_ITEMS = 20;
    const MAX_QUANTITY = 9999;

    let savedItems = [];
    try {
        savedItems = JSON.parse(list.dataset.items || "[]");
    } catch {
        savedItems = [];
    }

    const getRows = () => Array.from(list.querySelectorAll(".item-row"));
    const nameOf = (row) => row.querySelector(".item-name");
    const quantityOf = (row) => row.querySelector(".item-qty");
    const isFilled = (row) => nameOf(row).value.trim() !== "";

    // whole numbers from 1 to 9999; anything else becomes the nearest valid value
    const cleanQuantity = (value) => {
        const number = parseInt(String(value).replace(/\D/g, ""), 10);
        if (!number || number < 1) return 1;
        return Math.min(number, MAX_QUANTITY);
    };

    const createRow = (name, quantity) => {
        const row = document.createElement("div");
        row.className = "item-row";

        const nameInput = document.createElement("input");
        nameInput.type = "text";
        nameInput.name = "neededItemName";
        nameInput.className = "item-name";
        nameInput.maxLength = 100;
        nameInput.placeholder = "Item name";
        nameInput.autocomplete = "off";
        nameInput.value = name;

        const quantityInput = document.createElement("input");
        quantityInput.type = "text";
        quantityInput.inputMode = "numeric";
        quantityInput.name = "neededItemQuantity";
        quantityInput.className = "item-qty";
        quantityInput.maxLength = 4;
        quantityInput.placeholder = "Qty";
        quantityInput.autocomplete = "off";
        quantityInput.title = "Quantity needed";
        quantityInput.value = quantity;

        const removeButton = document.createElement("button");
        removeButton.type = "button";
        removeButton.className = "item-remove-button";
        removeButton.textContent = "Remove";

        row.append(nameInput, quantityInput, removeButton);
        return row;
    };

    // Keeps exactly one empty row at the end (until the limit), hides Remove on it,
    // and refreshes the screen-reader labels.
    const syncRows = () => {
        let rows = getRows();
        while (rows.length > 1 && !isFilled(rows[rows.length - 1]) && !isFilled(rows[rows.length - 2])) {
            rows[rows.length - 1].remove();
            rows = getRows();
        }
        if ((!rows.length || isFilled(rows[rows.length - 1])) && rows.length < MAX_ITEMS) {
            list.appendChild(createRow("", ""));
            rows = getRows();
        }

        rows.forEach((row, index) => {
            const isTrailingEmptyRow = index === rows.length - 1 && !isFilled(row);
            nameOf(row).setAttribute("aria-label", `Item ${index + 1} name`);
            quantityOf(row).setAttribute("aria-label", `Item ${index + 1} quantity`);
            const removeButton = row.querySelector(".item-remove-button");
            removeButton.hidden = isTrailingEmptyRow;
            removeButton.setAttribute("aria-label", `Remove item ${index + 1}`);
        });
    };

    savedItems.slice(0, MAX_ITEMS).forEach((item) => {
        list.appendChild(createRow(String(item.name ?? ""), String(item.quantity ?? "1")));
    });
    syncRows();

    list.addEventListener("input", (event) => {
        const field = event.target;
        if (!(field instanceof HTMLInputElement)) return;

        if (field.classList.contains("item-qty")) {
            field.value = field.value.replace(/\D/g, "");
            return;
        }

        // first letters typed into a new row: start it at 1 and add the next empty row
        if (field.classList.contains("item-name")) {
            const quantityInput = quantityOf(field.closest(".item-row"));
            if (field.value.trim() && !quantityInput.value) quantityInput.value = "1";
            syncRows();
        }
    });

    list.addEventListener("change", (event) => {
        const field = event.target;
        if (!(field instanceof HTMLInputElement) || !field.classList.contains("item-qty")) return;
        if (field.value || isFilled(field.closest(".item-row"))) {
            field.value = String(cleanQuantity(field.value));
        }
    });

    list.addEventListener("keydown", (event) => {
        const field = event.target;
        if (!(field instanceof HTMLInputElement)) return;
        const row = field.closest(".item-row");

        // Enter moves along the row instead of saving the whole fundraiser
        if (event.key === "Enter") {
            event.preventDefault();
            if (field.classList.contains("item-name")) {
                quantityOf(row).focus();
                quantityOf(row).select();
            } else {
                const nextRow = row.nextElementSibling;
                if (nextRow) nameOf(nextRow).focus();
            }
            return;
        }

        // Up / Down change the quantity by one
        if (field.classList.contains("item-qty") && ["ArrowUp", "ArrowDown"].includes(event.key)) {
            event.preventDefault();
            const step = event.key === "ArrowUp" ? 1 : -1;
            field.value = String(cleanQuantity((parseInt(field.value, 10) || 0) + step));
        }
    });

    list.addEventListener("click", (event) => {
        if (!(event.target instanceof Element)) return;
        const removeButton = event.target.closest(".item-remove-button");
        if (!removeButton) return;

        const row = removeButton.closest(".item-row");
        const rows = getRows();
        const neighbour = rows[rows.indexOf(row) + 1] || rows[rows.indexOf(row) - 1];
        row.remove();
        syncRows();

        // keyboard users keep their place; a tap on a phone does not pop the keyboard open
        if (event.detail === 0) {
            const target = neighbour && neighbour.isConnected ? neighbour : getRows().pop();
            if (target) nameOf(target).focus();
        }
    });
});

/*Campaign page: donor message character counter*/
document.addEventListener("DOMContentLoaded", () => {
    const commentText = document.getElementById("commentText");
    const commentCounter = document.getElementById("commentCounter");
    if (!commentText || !commentCounter) return;

    const max = Number(commentCounter.dataset.max) || commentText.maxLength;
    const updateCounter = () => {
        commentCounter.textContent = `${commentText.value.length} / ${max}`;
        commentCounter.classList.toggle("is-near-limit", commentText.value.length >= max - 50);
    };

    commentText.addEventListener("input", updateCounter);
    updateCounter();
});

/* ---------------------------------------------------------------
   Confirm before a form is sent. Put data-confirm on any <form>:
     data-confirm-title, data-confirm (the message), data-confirm-label,
     data-confirm-danger (red button). Used for cash out and for
     marking a parcel received / not received.
------------------------------------------------------------------ */
(function () {
    let overlay = null;
    let pendingForm = null;
    let opener = null;

    function build() {
        overlay = document.createElement("div");
        overlay.className = "popup-overlay confirm-overlay";
        overlay.hidden = true;
        overlay.innerHTML =
            '<div class="confirm-box" role="alertdialog" aria-modal="true" aria-labelledby="confirmTitle" aria-describedby="confirmText">' +
            '<span class="confirm-icon" aria-hidden="true"><i class="fa-solid fa-circle-question"></i></span>' +
            '<h2 id="confirmTitle"></h2>' +
            '<p id="confirmText"></p>' +
            '<div class="confirm-actions">' +
            '<button type="button" class="confirm-cancel">Cancel</button>' +
            '<button type="button" class="confirm-yes"></button>' +
            "</div></div>";
        document.body.appendChild(overlay);

        overlay.addEventListener("click", (event) => {
            if (event.target === overlay) close();
        });
        overlay.querySelector(".confirm-cancel").addEventListener("click", close);
        overlay.querySelector(".confirm-yes").addEventListener("click", () => {
            const form = pendingForm;
            if (!form) return;
            overlay.querySelector(".confirm-yes").disabled = true;
            form.submit();   // submit() skips the submit event, so this doesn't ask again
        });
        overlay.addEventListener("keydown", (event) => {
            if (event.key === "Escape") {
                close();
            } else if (event.key === "Tab") {
                // keep focus inside the dialog
                const buttons = Array.from(overlay.querySelectorAll("button:not(:disabled)"));
                const first = buttons[0];
                const last = buttons[buttons.length - 1];
                if (event.shiftKey && document.activeElement === first) {
                    event.preventDefault();
                    last.focus();
                } else if (!event.shiftKey && document.activeElement === last) {
                    event.preventDefault();
                    first.focus();
                }
            }
        });
    }

    function open(form, trigger) {
        if (!overlay) build();
        pendingForm = form;
        opener = trigger || null;

        const danger = form.hasAttribute("data-confirm-danger");
        const box = overlay.querySelector(".confirm-box");
        box.classList.toggle("is-danger", danger);
        box.querySelector(".confirm-icon i").className =
            "fa-solid " + (danger ? "fa-triangle-exclamation" : "fa-circle-question");
        overlay.querySelector("#confirmTitle").textContent = form.dataset.confirmTitle || "Are you sure?";
        overlay.querySelector("#confirmText").textContent = form.dataset.confirm || "";
        const yes = overlay.querySelector(".confirm-yes");
        yes.textContent = form.dataset.confirmLabel || "Yes";
        yes.disabled = false;

        overlay.hidden = false;
        // a safe default: Cancel has focus when the action is the risky one
        (danger ? overlay.querySelector(".confirm-cancel") : yes).focus();
    }

    function close() {
        if (!overlay) return;
        overlay.hidden = true;
        pendingForm = null;
        if (opener && opener.focus) opener.focus();
    }

    document.addEventListener("submit", (event) => {
        const form = event.target;
        if (!(form instanceof HTMLFormElement) || !form.hasAttribute("data-confirm")) return;
        event.preventDefault();
        open(form, event.submitter);
    });
})();

/* ---------------------------------------------------------------
   Admin: remove-account dialog (audit page). Needs a reason and a proof file.
------------------------------------------------------------------ */
(function () {
    const modal = document.getElementById("removeModal");
    if (!modal) return;

    const form = document.getElementById("removeForm");
    const title = document.getElementById("removeTitle");
    const intro = document.getElementById("removeIntro");
    const effects = document.getElementById("removeEffects");
    const reason = document.getElementById("removeReason");
    const proof = document.getElementById("removeProof");
    const count = document.getElementById("removeCount");
    const error = document.getElementById("removeError");
    const submit = document.getElementById("removeSubmit");
    const cancel = document.getElementById("removeCancel");

    const MIN_REASON = Number(reason.getAttribute("minlength")) || 10;
    const MAX_BYTES = 5 * 1024 * 1024;
    let opener = null;

    function check() {
        const text = reason.value.trim().replace(/\s+/g, " ");
        const file = proof.files && proof.files[0];
        let problem = "";
        if (file && !/\.(jpe?g|png|webp|pdf)$/i.test(file.name)) {
            problem = "Evidence must be a JPG, PNG, WebP or PDF file.";
        } else if (file && file.size > MAX_BYTES) {
            problem = "That file is over 5 MB. Choose a smaller one.";
        }
        count.textContent = reason.value.length;
        error.textContent = problem;
        error.hidden = !problem;
        submit.disabled = !(text.length >= MIN_REASON && file && !problem);
    }

    function open(button) {
        opener = button;
        const data = button.dataset;
        form.action = data.removeUrl;
        title.textContent = "Remove " + data.removeName + "?";
        intro.textContent = data.removeEmail + " (" + data.removeRole + ")";

        const lines = [
            "They are signed out straight away and can't log in again.",
            "Their donations and records stay, so the audit trail is complete.",
        ];
        if (data.removeRole === "ngo") {
            lines.push("Their fundraisers come off the public site.");
            if (data.removeUnpaid && data.removeUnpaid !== "0.00") {
                lines.push("RM " + data.removeUnpaid + " of their payouts is still unpaid. Settle that separately.");
            }
        }
        lines.push("Your reason and evidence are saved to the audit log under your name.");
        effects.replaceChildren(...lines.map((text) => {
            const li = document.createElement("li");
            li.textContent = text;
            return li;
        }));

        form.reset();
        check();
        modal.hidden = false;
        reason.focus();
    }

    function close() {
        modal.hidden = true;
        form.reset();
        if (opener) opener.focus();
    }

    document.addEventListener("click", (event) => {
        const button = event.target.closest(".audit-remove");
        if (button) open(button);
    });
    modal.addEventListener("click", (event) => {
        if (event.target === modal) close();
    });
    cancel.addEventListener("click", close);
    document.addEventListener("keydown", (event) => {
        if (event.key === "Escape" && !modal.hidden) close();
    });
    reason.addEventListener("input", check);
    proof.addEventListener("change", check);
    form.addEventListener("submit", (event) => {
        check();
        if (submit.disabled) {
            event.preventDefault();
            return;
        }
        submit.disabled = true;   // one click, one removal
    });
})();

/* ---------------------------------------------------------------
   Equipment donation form: quantity limit, courier / tracking, proof check
------------------------------------------------------------------ */
(function () {
    const form = document.getElementById("equipForm");
    if (!form) return;

    const item = document.getElementById("equipItem");
    const qty = document.getElementById("equipQty");
    const hint = document.getElementById("equipQtyHint");
    const courier = document.getElementById("equipCourier");
    const tracking = document.getElementById("equipTracking");
    const trackingOptional = document.getElementById("equipTrackingOptional");
    const proof = document.getElementById("equipProof");
    const proofOptional = document.getElementById("equipProofOptional");
    const proofHint = document.getElementById("equipProofHint");
    const proofHintHand = document.getElementById("equipProofHintHand");
    const drop = document.getElementById("equipDrop");
    const dropZone = document.getElementById("equipDropZone");
    const dropFile = document.getElementById("equipDropFile");
    const dropThumb = document.getElementById("equipDropThumb");
    const dropName = document.getElementById("equipDropName");
    const dropSize = document.getElementById("equipDropSize");
    const dropRemove = document.getElementById("equipDropRemove");
    const dropIdle = drop.querySelector(".equip-drop-idle");
    const dropOver = drop.querySelector(".equip-drop-over");
    let previewUrl = null;
    const error = document.getElementById("equipError");
    const submit = document.getElementById("equipSubmit");
    const hand = form.dataset.handDelivery;
    const trackingPlaceholder = tracking.placeholder;
    const MAX_BYTES = 5 * 1024 * 1024;
    const submitLabel = submit.textContent;

    const show = (text) => {
        error.textContent = text;
        error.hidden = !text;
    };
    const leftFor = () => {
        const option = item.selectedOptions[0];
        return option && option.dataset.left ? Number(option.dataset.left) : 0;
    };

    function syncItem() {
        const left = leftFor();
        if (left > 0) {
            qty.max = left;
            if (Number(qty.value) > left) qty.value = left;
            hint.textContent = left + " still needed, so you can send up to " + left + ".";
        } else {
            qty.removeAttribute("max");
            hint.textContent = "Pick an item to see how many are needed.";
        }
    }

    function syncCourier() {
        const byHand = courier.value === hand;
        // Hand delivery may skip the tracking number, but one that is typed is still checked
        tracking.required = !byHand && courier.value !== "";
        trackingOptional.hidden = !byHand;
        tracking.placeholder = byHand ? "Only if you have one" : trackingPlaceholder;

        // Nothing is posted when the donor hands it over, so there is no receipt to attach
        proof.required = !byHand;
        proofOptional.hidden = !byHand;
        proofHint.hidden = byHand;
        proofHintHand.hidden = !byHand;
    }

    const fileProblem = (file) => {
        if (!/\.(jpe?g|png|webp|pdf)$/i.test(file.name)) return "Proof must be a JPG, PNG, WebP or PDF file.";
        if (file.size > MAX_BYTES) return "That proof file is over 5 MB. Choose a smaller one.";
        return "";
    };

    const niceSize = (bytes) => bytes < 1024 * 1024
        ? Math.max(1, Math.round(bytes / 1024)) + " KB"
        : (bytes / (1024 * 1024)).toFixed(1) + " MB";

    // Draw the drop box or the chosen-file card, whichever matches what the file input holds
    function drawProof() {
        const file = proof.files && proof.files[0];
        if (previewUrl) {
            URL.revokeObjectURL(previewUrl);
            previewUrl = null;
        }
        dropThumb.textContent = "";
        dropFile.hidden = !file;
        dropZone.hidden = !!file;
        if (!file) return;

        dropName.textContent = file.name;
        dropName.title = file.name;
        dropSize.textContent = niceSize(file.size);
        if (/\.(jpe?g|png|webp)$/i.test(file.name)) {
            const picture = new Image();
            previewUrl = URL.createObjectURL(file);
            picture.src = previewUrl;
            picture.alt = "";
            // a file that is named .jpg but isn't a picture falls back to a plain icon
            picture.onerror = () => { dropThumb.innerHTML = '<i class="fa-solid fa-image"></i>'; };
            dropThumb.appendChild(picture);
        } else {
            dropThumb.innerHTML = '<i class="fa-solid fa-file-pdf"></i>';
        }
    }

    // A file from the picker or from a drop. One that can't be used is turned away straight away.
    function takeProof() {
        const file = proof.files && proof.files[0];
        const message = file ? fileProblem(file) : "";
        if (message) proof.value = "";
        drop.classList.toggle("is-error", !!message);
        show(message);
        drawProof();
        // the message sits lower in the form, so bring it into view on a small screen
        if (message) error.scrollIntoView({ block: "nearest", behavior: "smooth" });
    }

    function problem() {
        if (!item.value) return "Choose the item you are sending.";
        const left = leftFor();
        const amount = Number(qty.value);
        if (!Number.isInteger(amount) || amount < 1) return "Enter how many you are sending.";
        if (left > 0 && amount > left) return "Only " + left + " more are needed. Lower the quantity.";
        if (!courier.value) return "Choose the courier carrying your parcel.";
        const trackingNo = tracking.value.replace(/\s+/g, "");
        if ((courier.value !== hand || trackingNo) && !/^[A-Z0-9-]{6,40}$/i.test(trackingNo)) {
            return "Enter the tracking number from your receipt (6 to 40 letters, numbers or dashes).";
        }
        const file = proof.files && proof.files[0];
        if (!file && courier.value !== hand) return "Attach a photo or PDF of your receipt or delivery proof.";
        // a file that is attached is always checked, even when proof is optional
        if (file && !/\.(jpe?g|png|webp|pdf)$/i.test(file.name)) return "Proof must be a JPG, PNG, WebP or PDF file.";
        if (file && file.size > MAX_BYTES) return "That proof file is over 5 MB. Choose a smaller one.";
        return "";
    }

    item.addEventListener("change", () => { syncItem(); show(""); });
    courier.addEventListener("change", () => { syncCourier(); show(""); });
    [qty, tracking].forEach((el) => el.addEventListener("input", () => show("")));
    proof.addEventListener("change", takeProof);

    dropRemove.addEventListener("click", () => {
        proof.value = "";
        drop.classList.remove("is-error");
        show("");
        drawProof();
        proof.focus();
    });

    // Drag a file over the box, or onto the chosen-file card to swap it
    const carriesFiles = (event) => !!event.dataTransfer
        && Array.from(event.dataTransfer.types || []).includes("Files");
    const setOver = (on) => {
        drop.classList.toggle("is-over", on);
        dropIdle.hidden = on;
        dropOver.hidden = !on;
    };
    ["dragenter", "dragover"].forEach((name) => drop.addEventListener(name, (event) => {
        if (!carriesFiles(event)) return;
        event.preventDefault();
        event.dataTransfer.dropEffect = "copy";
        setOver(true);
    }));
    drop.addEventListener("dragleave", (event) => {
        if (!drop.contains(event.relatedTarget)) setOver(false);
    });
    drop.addEventListener("drop", (event) => {
        if (!carriesFiles(event)) return;
        event.preventDefault();
        setOver(false);
        const dropped = event.dataTransfer.files[0];
        if (!dropped) return;
        try {
            const one = new DataTransfer();
            one.items.add(dropped);
            proof.files = one.files;
        } catch (err) {
            proof.files = event.dataTransfer.files;
        }
        takeProof();
    });
    // A file let go outside the box would make the browser open it and lose the form
    ["dragover", "drop"].forEach((name) => window.addEventListener(name, (event) => {
        if (carriesFiles(event)) event.preventDefault();
    }));

    form.addEventListener("submit", (event) => {
        const message = problem();
        if (message) {
            event.preventDefault();
            show(message);
            return;
        }
        submit.disabled = true;
        submit.textContent = "Sending...";
    });

    // coming back with the browser's Back button should not leave the button stuck
    window.addEventListener("pageshow", () => {
        submit.disabled = false;
        submit.textContent = submitLabel;
    });

    syncItem();
    syncCourier();
    drawProof();
})();

/* ---------------------------------------------------------------
   Copy a tracking number
------------------------------------------------------------------ */
document.addEventListener("click", (event) => {
    const button = event.target.closest("[data-copy]");
    if (!button) return;

    const text = button.dataset.copy;
    const done = () => {
        const icon = button.querySelector("i");
        button.classList.add("is-copied");
        button.title = "Copied";
        if (icon) icon.className = "fa-solid fa-check";
        setTimeout(() => {
            button.classList.remove("is-copied");
            button.title = "Copy tracking number";
            if (icon) icon.className = "fa-regular fa-copy";
        }, 1500);
    };

    if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(text).then(done).catch(() => {});
    } else {
        const box = document.createElement("textarea");
        box.value = text;
        box.style.position = "fixed";
        box.style.opacity = "0";
        document.body.appendChild(box);
        box.select();
        try { document.execCommand("copy"); done(); } catch (e) { /* nothing to do */ }
        box.remove();
    }
});

/* ---------------------------------------------------------------
   Admin: register NGO. Makes a strong temporary password.
------------------------------------------------------------------ */
document.addEventListener("DOMContentLoaded", () => {
    const button = document.getElementById("generatePassword");
    const password = document.getElementById("ngoPassword");
    const confirmField = document.getElementById("ngoConfirm");
    if (!button || !password || !confirmField) return;

    const pick = (chars) => {
        const bytes = new Uint32Array(1);
        crypto.getRandomValues(bytes);
        return chars[bytes[0] % chars.length];
    };
    const LETTERS = "abcdefghijkmnopqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ";
    const DIGITS = "23456789";
    const SYMBOLS = "!#@%*?";

    button.addEventListener("click", () => {
        // two of each kind guarantees the server's "letters plus a number or symbol" rule
        const chars = [
            pick(DIGITS), pick(DIGITS), pick(SYMBOLS), pick(SYMBOLS),
            ...Array.from({ length: 8 }, () => pick(LETTERS)),
        ];
        for (let i = chars.length - 1; i > 0; i--) {   // shuffle
            const bytes = new Uint32Array(1);
            crypto.getRandomValues(bytes);
            const j = bytes[0] % (i + 1);
            [chars[i], chars[j]] = [chars[j], chars[i]];
        }
        const value = chars.join("");
        password.type = "text";
        confirmField.type = "text";
        password.value = value;
        confirmField.value = value;

        const label = button.innerHTML;
        const finish = (text) => {
            button.textContent = text;
            setTimeout(() => { button.innerHTML = label; }, 1800);
        };
        if (navigator.clipboard && navigator.clipboard.writeText) {
            navigator.clipboard.writeText(value).then(() => finish("Copied"), () => finish("Generated"));
        } else {
            finish("Generated");
        }
    });
});

// ---------------------------------------------------------------------------
// Ranking (profile.html): "See full ranking" loads the rest of the board a page at a time
// ---------------------------------------------------------------------------
document.addEventListener("DOMContentLoaded", () => {
    const more = document.querySelector("[data-rank-more]");
    const list = document.getElementById("rankList");
    if (!more || !list) return;

    const less = document.querySelector("[data-rank-less]");
    const tail = document.querySelector("[data-rank-tail]");        // "..." and your own row, when you are further down
    const errorBox = document.querySelector("[data-rank-error]");
    const status = document.querySelector("[data-rank-status]");
    const label = more.querySelector("[data-label]");
    const count = more.querySelector("[data-count]");
    const startNext = Number(more.dataset.next);
    const startLabel = label.textContent;
    const startCount = count.textContent;
    const total = Number(more.dataset.total);
    const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    let next = startNext;
    let busy = false;

    function setError(text) {
        errorBox.textContent = text;
        errorBox.hidden = !text;
    }

    async function loadMore() {
        if (busy || next === null) return;
        busy = true;
        more.disabled = true;
        setError("");
        const shown = label.textContent;
        label.textContent = "Loading…";
        try {
            const response = await fetch(`${more.dataset.url}?offset=${next}`, {
                headers: { Accept: "application/json" },
                credentials: "same-origin",
            });
            const data = await response.json().catch(() => ({}));
            if (!response.ok) throw new Error(data.error || "The ranking could not be loaded.");

            list.insertAdjacentHTML("beforeend", data.html);
            next = data.next;
            more.setAttribute("aria-expanded", "true");
            less.hidden = false;
            // once your own row is in the list the "..." shortcut is not needed
            if (data.has_you && tail) tail.hidden = true;

            label.textContent = "Show more";
            if (next === null) {
                more.hidden = true;
            } else {
                count.textContent = `${total - next} more`;
            }
            if (status) status.textContent = `Showing ${data.shown} of ${data.total} donors.`;
            if (data.has_you) {
                const mine = list.querySelector("[data-extra][data-you]");
                if (mine) mine.scrollIntoView({ block: "center", behavior: reduceMotion ? "auto" : "smooth" });
            }
        } catch (error) {
            label.textContent = shown;
            setError(error.message === "Failed to fetch" ? "No connection. Check your internet and try again." : error.message);
        } finally {
            busy = false;
            more.disabled = false;
        }
    }

    function showLess() {
        list.querySelectorAll("[data-extra]").forEach((row) => row.remove());
        next = startNext;
        if (tail) tail.hidden = false;
        more.hidden = false;
        more.setAttribute("aria-expanded", "false");
        label.textContent = startLabel;
        count.textContent = startCount;
        less.hidden = true;
        setError("");
        if (status) status.textContent = "Showing the top of the ranking.";
        const card = document.getElementById("ranking");
        if (card) card.scrollIntoView({ block: "start", behavior: reduceMotion ? "auto" : "smooth" });
        more.focus({ preventScroll: true });
    }

    more.addEventListener("click", loadMore);
    less.addEventListener("click", showLess);
});

/* ===================================================================== */
/* Updates: the nav badge (every page) and the Updates page itself.       */
/* ===================================================================== */
(function () {
    "use strict";

    const UNREAD_URL = "/updates/unread";
    const POLL_MS = 60000;

    function paintBadge(count) {
        document.querySelectorAll("[data-updates-badge]").forEach((badge) => {
            badge.textContent = count > 9 ? "9+" : String(count);
            badge.title = count + " unread";
            badge.hidden = count < 1;
        });
    }

    function start() {
        const app = document.getElementById("updApp");
        const hasBadge = document.querySelector("[data-updates-badge]");
        if (!app && !hasBadge) return;

        let onCount = null;     // the Updates page listens for a changed unread count

        function poll() {
            if (document.hidden) return;
            fetch(UNREAD_URL, { headers: { "X-Requested-With": "XMLHttpRequest" }, cache: "no-store" })
                .then((response) => (response.ok ? response.json() : null))
                .then((data) => {
                    if (!data) return;
                    paintBadge(data.unread);
                    if (onCount) onCount(data.unread);
                })
                .catch(() => { /* offline: try again next time */ });
        }

        setInterval(poll, POLL_MS);
        document.addEventListener("visibilitychange", () => { if (!document.hidden) poll(); });

        if (!app) return;

        /* ---------------- the Updates page ---------------- */
        app.classList.add("is-live");

        const feed = document.getElementById("updFeed");
        const filters = document.getElementById("updFilters");
        const summary = document.getElementById("updSummary");
        const markAll = document.getElementById("updMarkAll");
        const clearLink = document.getElementById("updClear");
        const newBanner = document.getElementById("updNew");
        const toastBox = document.getElementById("updToast");
        const toastText = document.getElementById("updToastText");
        const toastUndo = document.getElementById("updToastUndo");
        const photoDialog = document.getElementById("updPhoto");
        let unread = Number(app.dataset.unread) || 0;
        let view = app.dataset.view || "all";
        let toastTimer = null;
        let loadId = 0;
        let loading = null;

        const post = (url, data) =>
            fetch(url, {
                method: "POST",
                headers: { "X-Requested-With": "XMLHttpRequest", "X-CSRF-Token": app.dataset.token },
                body: new URLSearchParams(data),
            }).then((response) =>
                response.json().catch(() => ({})).then((json) => ({ ...json, ok: response.ok && json.ok !== false })));

        function toast(text, undo) {
            clearTimeout(toastTimer);
            toastText.textContent = text;
            toastUndo.hidden = !undo;
            toastUndo.onclick = undo ? () => { hideToast(); undo(); } : null;
            toastBox.hidden = false;
            toastTimer = setTimeout(hideToast, 8000);
        }

        function hideToast() {
            clearTimeout(toastTimer);
            toastBox.hidden = true;
        }

        function sentView() {
            return view === "sent" || view === "all_sent";
        }

        function setUnread(count) {
            unread = count;
            app.dataset.unread = String(count);
            paintBadge(count);
            if (!sentView()) {
                summary.textContent = count
                    ? "You have " + count + " unread update" + (count === 1 ? "" : "s") + "."
                    : "You are all caught up.";
            }
            if (markAll) markAll.disabled = count < 1;
            const tabCount = app.querySelector(".upd-tab[data-view='unread']");
            if (tabCount) {
                let chip = tabCount.querySelector(".upd-tab-count");
                if (count && !chip) {
                    chip = document.createElement("span");
                    chip.className = "upd-tab-count";
                    tabCount.append(" ", chip);
                }
                if (chip) {
                    if (count) chip.textContent = String(count); else chip.remove();
                }
            }
        }

        /* A long announcement shows three lines and a "Read more" button, but only when it needs one. */
        function clamp(root) {
            root.querySelectorAll(".upd-body[data-clamp]").forEach((body) => {
                const more = body.parentElement.querySelector(".upd-expand");
                if (more && more.getAttribute("aria-expanded") === "true") return;   // the reader opened this one
                body.classList.add("is-clamped");
                const overflow = body.scrollHeight > body.clientHeight + 1;
                if (more) more.hidden = !overflow;
                if (!overflow) body.classList.remove("is-clamped");
            });
        }

        /* ---------- search, dates and type: the list updates as you go ---------- */
        function query() {
            const params = new URLSearchParams();
            new FormData(filters).forEach((value, key) => { if (String(value).trim()) params.set(key, String(value).trim()); });
            params.set("view", view);
            return params;
        }

        function paintFilters(params) {
            const active = ["q", "start", "end", "cat"].some((key) => params.has(key));
            if (clearLink) clearLink.hidden = !active;
        }

        function load() {
            const params = query();
            const mine = ++loadId;
            if (loading) loading.abort();
            loading = new AbortController();
            history.replaceState(null, "", "?" + params.toString());
            paintFilters(params);
            return fetch(app.dataset.feed + "?" + params.toString(), {
                headers: { "X-Requested-With": "XMLHttpRequest" },
                signal: loading.signal,
            })
                .then((response) => {
                    if (response.status === 401) { location.reload(); return null; }
                    if (!response.ok) throw new Error("feed");
                    return response.text().then((html) => ({ html, count: response.headers.get("X-Updates-Unread") }));
                })
                .then((result) => {
                    if (!result || mine !== loadId) return;
                    feed.innerHTML = result.html;
                    feed.removeAttribute("aria-busy");
                    requestAnimationFrame(() => clamp(feed));
                    if (result.count !== null) setUnread(Number(result.count));
                })
                .catch((error) => {
                    if (error.name === "AbortError") return;
                    toast("Could not load updates. Check your connection and try again.");
                });
        }

        let typing = null;
        filters.addEventListener("input", (event) => {
            if (event.target.name === "q") {
                clearTimeout(typing);
                typing = setTimeout(load, 250);
            }
        });
        filters.addEventListener("change", (event) => {
            if (event.target.name !== "q") load();
        });
        filters.addEventListener("submit", (event) => { event.preventDefault(); load(); });

        function resetFilters() {
            filters.querySelectorAll("input[type='search'], input[type='date']").forEach((input) => { input.value = ""; });
            const type = filters.elements["cat"];
            if (type) type.value = "";
        }

        if (clearLink) {
            clearLink.addEventListener("click", (event) => { event.preventDefault(); resetFilters(); load(); });
        }

        app.querySelector(".upd-tabs").addEventListener("click", (event) => {
            const tab = event.target.closest(".upd-tab");
            if (!tab) return;
            event.preventDefault();
            view = tab.dataset.view;
            filters.elements["view"].value = view;
            app.dataset.view = view;
            app.querySelectorAll(".upd-tab").forEach((other) => {
                other.classList.toggle("is-active", other === tab);
                if (other === tab) other.setAttribute("aria-current", "page"); else other.removeAttribute("aria-current");
            });
            // Type only applies to what you receive
            const typeField = filters.elements["cat"];
            if (typeField) {
                typeField.closest(".upd-field").hidden = sentView();
                if (sentView()) typeField.value = "";
            }
            if (sentView()) {
                summary.textContent = view === "all_sent"
                    ? "Every announcement on MEDonate. You can retract any of them."
                    : "Announcements you have sent. Retract one and it disappears from everyone's updates.";
            } else {
                setUnread(unread);
            }
            if (markAll) markAll.hidden = sentView();
            load();
        });

        /* ---------- reading and un-reading ---------- */
        function paintRead(item, read) {
            item.classList.toggle("is-read", read);
            item.classList.toggle("is-unread", !read);
            const flip = item.querySelector(".upd-flip");
            if (flip) flip.textContent = read ? "Mark as unread" : "Mark as read";
        }

        function markItem(item, read) {
            if (item.classList.contains("is-read") === read) return;
            paintRead(item, read);
            post("/updates/mark", { to: read ? "read" : "unread", id: item.dataset.id }).then((result) => {
                if (!result.ok) {
                    paintRead(item, !read);
                    toast(result.message || "Could not save that. Try again.");
                    return;
                }
                setUnread(result.unread);
            });
        }

        markAll.addEventListener("click", () => {
            if (!unread) return;
            const wasUnread = unread;
            feed.querySelectorAll(".upd-item.is-unread").forEach((item) => paintRead(item, true));
            post("/updates/mark", { to: "all" }).then((result) => {
                if (!result.ok) {
                    feed.querySelectorAll(".upd-item").forEach((item) => paintRead(item, item.classList.contains("is-read")));
                    toast(result.message || "Could not mark everything as read.");
                    return;
                }
                setUnread(0);
                if (view === "unread") load();
                toast(wasUnread === 1 ? "1 update marked as read." : wasUnread + " updates marked as read.", () => {
                    post("/updates/mark", { to: "undo" }).then((undone) => {
                        if (undone.ok) { setUnread(undone.unread); load(); }
                    });
                });
            });
        });

        /* ---------- clicks inside the list ---------- */
        let retractTimer = null;

        feed.addEventListener("click", (event) => {
            const control = event.target.closest("[data-act]");
            const item = event.target.closest(".upd-item");

            if (!control) {
                // Opening an unread update by clicking it counts as reading it (not links: the server marks those)
                if (item && !event.target.closest("a") && item.classList.contains("is-unread") && !sentView()) {
                    markItem(item, true);
                }
                return;
            }

            const act = control.dataset.act;

            if (act === "flip") {
                markItem(item, !item.classList.contains("is-read"));
            } else if (act === "expand") {
                const body = item.querySelector(".upd-body");
                const open = body.classList.toggle("is-clamped") === false;
                control.textContent = open ? "Show less" : "Read more";
                control.setAttribute("aria-expanded", String(open));
                if (open && !sentView()) markItem(item, true);
            } else if (act === "photo") {
                photoDialog.querySelector("img").src = control.dataset.src;
                photoDialog.showModal();
                if (!sentView()) markItem(item, true);
            } else if (act === "older") {
                control.disabled = true;
                fetch(control.dataset.url, { headers: { "X-Requested-With": "XMLHttpRequest" } })
                    .then((response) => { if (!response.ok) throw new Error("older"); return response.text(); })
                    .then((html) => {
                        const holder = document.createElement("template");
                        holder.innerHTML = html;
                        control.closest(".upd-older").remove();
                        const lead = holder.content.querySelector(".upd-day[data-continued]");
                        if (lead) {
                            const lists = feed.querySelectorAll(".upd-list");
                            const last = lists[lists.length - 1];
                            if (last) last.append(...lead.querySelectorAll(".upd-item"));
                            lead.remove();
                        }
                        feed.append(holder.content);
                        clamp(feed);
                    })
                    .catch(() => { control.disabled = false; toast("Could not load older updates. Try again."); });
            } else if (act === "clear") {
                event.preventDefault();
                resetFilters();
                load();
            } else if (act === "retract") {
                // two steps in place, so a stray click cannot take an announcement back
                if (control.dataset.sure !== "1") {
                    control.dataset.sure = "1";
                    control.textContent = "Confirm: retract for everyone";
                    clearTimeout(retractTimer);
                    retractTimer = setTimeout(() => { control.dataset.sure = ""; control.textContent = "Retract"; }, 4000);
                    return;
                }
                control.disabled = true;
                post(app.dataset.retract.replace("ID", item.dataset.id), {}).then((result) => {
                    if (!result.ok) {
                        control.disabled = false;
                        control.dataset.sure = "";
                        control.textContent = "Retract";
                        toast(result.message || "Could not retract that.");
                        return;
                    }
                    const day = item.closest(".upd-day");
                    item.remove();
                    if (day && !day.querySelector(".upd-item")) day.remove();
                    toast("Announcement retracted.");
                    if (!feed.querySelector(".upd-item")) load();
                });
            }
        });

        photoDialog.addEventListener("click", (event) => {
            if (event.target === photoDialog || event.target.closest("[data-close]")) photoDialog.close();
        });

        /* ---------- new things arrived while the page was open ---------- */
        onCount = (count) => {
            if (count > unread && newBanner) {
                const extra = count - unread;
                newBanner.textContent = extra === 1 ? "1 new update. Show it" : extra + " new updates. Show them";
                newBanner.hidden = false;
            }
        };
        if (newBanner) {
            newBanner.addEventListener("click", () => { newBanner.hidden = true; load(); });
        }

        /* ---------- write an announcement ---------- */
        const dialog = document.getElementById("updCompose");
        if (dialog) {
            const form = document.getElementById("updComposeForm");
            const reach = document.getElementById("updReach");
            const send = document.getElementById("updSend");
            const error = document.getElementById("updComposeError");
            const audienceUrl = app.dataset.audience;
            const picks = form.querySelectorAll("select[name='campaign_id'], select[name='ngo_id']");
            let reachId = 0;

            function showError(message) {
                error.textContent = message || "";
                error.hidden = !message;
            }

            function syncPicker() {
                const chosen = form.querySelector("input[name='audience']:checked").value;
                picks.forEach((select) => {
                    const needed = (select.name === "campaign_id" && chosen === "campaign")
                        || (select.name === "ngo_id" && (chosen === "ngo_donors" || chosen === "ngo"));
                    select.hidden = !needed;
                });
            }

            function sendLabel(count) {
                const chosen = form.querySelector("input[name='audience']:checked").value;
                const noun = chosen === "ngo" ? "NGO" : (chosen === "ngos" ? "NGOs" : (count === 1 ? "person" : "people"));
                return "Send to " + count + " " + noun;
            }

            function checkReach() {
                const mine = ++reachId;
                const params = new URLSearchParams();
                params.set("audience", form.querySelector("input[name='audience']:checked").value);
                picks.forEach((select) => { if (!select.hidden) params.set(select.name, select.value); });
                reach.textContent = "Counting who this reaches";
                send.disabled = true;
                fetch(audienceUrl + "?" + params.toString(), { headers: { "X-Requested-With": "XMLHttpRequest" } })
                    .then((response) => response.json())
                    .then((data) => {
                        if (mine !== reachId) return;
                        if (!data.ok) { reach.textContent = data.message || ""; send.textContent = "Send"; return; }
                        if (data.count === 0) {
                            reach.textContent = data.label + ": no one yet.";
                            send.textContent = "Send";
                            return;
                        }
                        reach.textContent = data.label + ": " + data.count + (data.count === 1 ? " person" : " people") + " will get this.";
                        send.textContent = sendLabel(data.count);
                        send.disabled = false;
                    })
                    .catch(() => { if (mine === reachId) { reach.textContent = "Could not count the audience. You can still send."; send.disabled = false; } });
            }

            form.addEventListener("change", (event) => {
                if (event.target.name === "audience" || event.target.name === "campaign_id" || event.target.name === "ngo_id") {
                    syncPicker();
                    checkReach();
                }
            });

            form.querySelectorAll("[data-count-for]").forEach((counter) => {
                const field = document.getElementById(counter.dataset.countFor);
                const max = Number(counter.dataset.max);
                const paint = () => {
                    counter.textContent = field.value.length + "/" + max;
                    counter.classList.toggle("is-near", field.value.length >= max * 0.9);
                };
                field.addEventListener("input", paint);
                paint();
            });

            document.getElementById("updWrite").addEventListener("click", () => {
                showError("");
                syncPicker();
                dialog.showModal();
                form.elements["subject"].focus();
                checkReach();
            });

            dialog.addEventListener("click", (event) => {
                if (event.target === dialog || event.target.closest("[data-close]")) dialog.close();
            });

            form.addEventListener("submit", (event) => {
                event.preventDefault();
                showError("");
                send.disabled = true;
                fetch(form.getAttribute("action"), {
                    method: "POST",
                    headers: { "X-Requested-With": "XMLHttpRequest", "X-CSRF-Token": app.dataset.token },
                    body: new FormData(form),
                })
                    .then((response) => response.json().catch(() => ({})).then((json) => ({ ok: response.ok && json.ok !== false, ...json })))
                    .then((result) => {
                        if (result.ok && result.redirect) { location.href = result.redirect; return; }
                        showError(result.message || "Could not send that. Try again.");
                        send.disabled = false;
                    })
                    .catch(() => { showError("A connection error occurred. Please try again."); send.disabled = false; });
            });
        }

        setUnread(unread);
        requestAnimationFrame(() => clamp(feed));
        if (document.fonts && document.fonts.ready) document.fonts.ready.then(() => clamp(feed));
    }

    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start);
    else start();
})();