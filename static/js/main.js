const menuToggle = document.querySelector(".menu-toggle");
const mainNav = document.querySelector(".main-nav");
const contactLink = mainNav?.querySelector('a[href$="#contact"]');
const routeLink = mainNav?.querySelector('a[aria-current="page"]');

if (contactLink && routeLink) {
  const updateCurrentNav = () => {
    const activeLink = window.location.hash === "#contact" ? contactLink : routeLink;
    mainNav.querySelectorAll("a").forEach((link) => {
      const active = link === activeLink;
      link.classList.toggle("active", active);
      if (active) link.setAttribute("aria-current", "page");
      else link.removeAttribute("aria-current");
    });
  };

  updateCurrentNav();
  window.addEventListener("hashchange", updateCurrentNav);
}

document.querySelectorAll(".flash").forEach((message) => {
  const dismissDelay = message.classList.contains("flash-logout") ? 1600 : 2500;
  window.setTimeout(() => {
    message.classList.add("is-dismissing");
    window.setTimeout(() => message.remove(), 180);
  }, dismissDelay);
});

const orderNotification = document.querySelector("[data-order-notifications-url]");

if (orderNotification) {
  const notificationBadge = orderNotification.querySelector(".notification-count");
  const notificationPanel = document.querySelector("#order-notification-panel");
  const notificationList = notificationPanel?.querySelector("[data-order-notification-list]");
  const notificationEmpty = notificationPanel?.querySelector("[data-order-notification-empty]");
  const notificationToast = document.querySelector("#order-notification-toast");
  const notificationMessage = notificationToast?.querySelector("[data-order-notification-message]");
  const dismissNotification = notificationToast?.querySelector("[data-dismiss-order-notification]");
  let latestOrderId = Number(orderNotification.dataset.latestOrderId || "0");
  let notificationTimeout;

  const closeNotificationPanel = () => {
    if (!notificationPanel) return;
    notificationPanel.hidden = true;
    orderNotification.setAttribute("aria-expanded", "false");
  };

  orderNotification.addEventListener("click", () => {
    if (!notificationPanel) return;
    const shouldOpen = notificationPanel.hidden;
    notificationPanel.hidden = !shouldOpen;
    orderNotification.setAttribute("aria-expanded", String(shouldOpen));
    if (shouldOpen) checkForNewOrders();
  });

  document.addEventListener("click", (event) => {
    if (
      notificationPanel &&
      !notificationPanel.hidden &&
      !orderNotification.contains(event.target) &&
      !notificationPanel.contains(event.target)
    ) {
      closeNotificationPanel();
    }
  });

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") closeNotificationPanel();
  });

  const hideNotification = () => {
    if (notificationToast) notificationToast.hidden = true;
    window.clearTimeout(notificationTimeout);
  };

  const showNotification = (order) => {
    if (!notificationToast || !notificationMessage) return;
    notificationMessage.textContent = `May bagong order mula kay ${order.full_name} (Order #${order.id}).`;
    notificationToast.hidden = false;
    window.clearTimeout(notificationTimeout);
    notificationTimeout = window.setTimeout(hideNotification, 8000);
  };

  dismissNotification?.addEventListener("click", hideNotification);

  const checkForNewOrders = async () => {
    try {
      const response = await fetch(orderNotification.dataset.orderNotificationsUrl, {
        headers: { Accept: "application/json" },
        cache: "no-store",
      });
      if (!response.ok) {
        throw new Error(`Order notification request failed with status ${response.status}`);
      }

      const status = await response.json();
      const pendingCount = Number(status.pending_count);
      const currentLatestOrderId = Number(status.latest_order_id);
      if (
        !Number.isSafeInteger(pendingCount) ||
        pendingCount < 0 ||
        !Number.isSafeInteger(currentLatestOrderId) ||
        currentLatestOrderId < 0 ||
        !Array.isArray(status.orders)
      ) {
        throw new Error("Order notification response contained invalid values");
      }

      const newOrder = status.orders.find((order) => Number(order.id) === currentLatestOrderId);
      if (currentLatestOrderId > latestOrderId && newOrder) showNotification(newOrder);
      latestOrderId = currentLatestOrderId;
      notificationBadge.textContent = String(pendingCount);
      notificationBadge.hidden = pendingCount === 0;
      if (notificationList && notificationEmpty) {
        notificationList.replaceChildren();
        status.orders.forEach((order) => {
          const item = document.createElement("li");
          const link = document.createElement("a");
          const title = document.createElement("strong");
          const details = document.createElement("span");
          title.textContent = `Order #${order.id} — ${order.full_name}`;
          details.textContent = `Status: ${String(order.status).replace(/_/g, " ")}`;
          link.href = order.manage_url;
          link.className = "order-notification-item";
          link.append(title, details);
          item.append(link);
          notificationList.append(item);
        });
        notificationEmpty.hidden = status.orders.length > 0;
      }
      orderNotification.setAttribute(
        "aria-label",
        pendingCount ? `${pendingCount} new orders` : "Notifications",
      );
    } catch (error) {
      console.error("Could not check for new orders.", error);
    } finally {
      window.setTimeout(checkForNewOrders, 10000);
    }
  };

  window.setTimeout(checkForNewOrders, 10000);
}

if (menuToggle && mainNav) {
  menuToggle.addEventListener("click", () => {
    const expanded = menuToggle.getAttribute("aria-expanded") === "true";
    menuToggle.setAttribute("aria-expanded", String(!expanded));
    mainNav.classList.toggle("is-open", !expanded);
  });
}

const heroSlideshow = document.querySelector("[data-hero-slideshow]");

if (heroSlideshow) {
  const hero = heroSlideshow.closest(".hero");
  const slides = Array.from(heroSlideshow.querySelectorAll(".hero-slide"));
  const indicators = Array.from(document.querySelectorAll(".hero-dot"));
  const toggle = document.querySelector(".hero-toggle");
  const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");

  if (hero && slides.length > 1 && toggle instanceof HTMLButtonElement) {
    let activeIndex = 0;
    let paused = reducedMotion.matches;
    let hovered = false;
    let focused = false;
    let timerId;

    const showSlide = (index) => {
      activeIndex = (index + slides.length) % slides.length;
      slides.forEach((slide, slideIndex) => {
        slide.classList.toggle("is-active", slideIndex === activeIndex);
      });
      indicators.forEach((indicator, slideIndex) => {
        indicator.setAttribute("aria-pressed", String(slideIndex === activeIndex));
      });
    };

    const stopSlideshow = () => {
      window.clearInterval(timerId);
      timerId = undefined;
    };

    const startSlideshow = () => {
      stopSlideshow();
      if (!paused && !hovered && !focused && !document.hidden) {
        timerId = window.setInterval(() => showSlide(activeIndex + 1), 2000);
      }
    };

    const updateToggle = () => {
      toggle.setAttribute("aria-pressed", String(paused));
      toggle.setAttribute("aria-label", paused ? "Play slideshow" : "Pause slideshow");
      toggle.querySelector("span").textContent = paused ? "▶" : "Ⅱ";
    };

    indicators.forEach((indicator, index) => {
      indicator.addEventListener("click", () => {
        showSlide(index);
        startSlideshow();
      });
    });

    toggle.addEventListener("click", () => {
      paused = !paused;
      updateToggle();
      startSlideshow();
    });

    document.addEventListener("visibilitychange", startSlideshow);
    hero.addEventListener("pointerenter", () => {
      hovered = true;
      stopSlideshow();
    });
    hero.addEventListener("pointerleave", () => {
      hovered = false;
      startSlideshow();
    });
    hero.addEventListener("focusin", () => {
      focused = true;
      stopSlideshow();
    });
    hero.addEventListener("focusout", (event) => {
      if (!hero.contains(event.relatedTarget)) {
        focused = false;
        startSlideshow();
      }
    });

    updateToggle();
    startSlideshow();
  }
}

document.querySelectorAll(".password-toggle").forEach((toggle) => {
  toggle.addEventListener("click", () => {
    const input = document.getElementById(toggle.getAttribute("aria-controls"));
    if (!(input instanceof HTMLInputElement)) return;
    const reveal = input.type === "password";
    input.type = reveal ? "text" : "password";
    toggle.setAttribute("aria-pressed", String(reveal));
    toggle.setAttribute("aria-label", reveal ? "Hide password" : "Show password");
  });
});

document.querySelectorAll("form[data-confirm]").forEach((form) => {
  form.addEventListener("submit", (event) => {
    if (!window.confirm(form.dataset.confirm)) event.preventDefault();
  });
});

const sortSelect = document.querySelector("#sort-products");

if (sortSelect) {
  sortSelect.addEventListener("change", () => sortSelect.form?.requestSubmit());
}

const productImageInput = document.querySelector("#product-image-input");
const productImagePreview = document.querySelector("#product-image-preview");
const productImagePlaceholder = document.querySelector("#product-image-placeholder");
const productImageFilename = document.querySelector("#product-image-filename");

if (productImageInput && productImagePreview && productImagePlaceholder && productImageFilename) {
  let previewUrl = null;
  productImageInput.addEventListener("change", () => {
    const file = productImageInput.files?.[0];
    if (!file) return;
    if (previewUrl) URL.revokeObjectURL(previewUrl);
    previewUrl = URL.createObjectURL(file);
    productImagePreview.src = previewUrl;
    productImagePreview.hidden = false;
    productImagePlaceholder.hidden = true;
    productImageFilename.textContent = file.name;
  });
}

const galleryImageInput = document.querySelector("#gallery-image-input");
const galleryImagePreview = document.querySelector("#gallery-image-preview");
const galleryImageError = document.querySelector("#gallery-image-error");

if (
  galleryImageInput instanceof HTMLInputElement
  && galleryImagePreview
  && galleryImageError
) {
  const existingImages = JSON.parse(galleryImagePreview.dataset.existingImages || "[]");
  let selectedFiles = [];
  let previewUrls = [];

  const renderGalleryPreview = (images) => {
    previewUrls.forEach((url) => URL.revokeObjectURL(url));
    previewUrls = [];
    galleryImagePreview.replaceChildren();
    images.forEach((image, index) => {
      const preview = document.createElement("img");
      preview.alt = `Gallery image preview ${index + 1}`;
      if (image instanceof File) {
        const previewUrl = URL.createObjectURL(image);
        previewUrls.push(previewUrl);
        preview.src = previewUrl;
      } else {
        preview.src = image;
      }
      galleryImagePreview.append(preview);
    });
  };

  galleryImageInput.addEventListener("change", () => {
    const newFiles = Array.from(galleryImageInput.files || []);
    const combinedFiles = [...selectedFiles, ...newFiles];
    if (existingImages.length + combinedFiles.length > 3) {
      const previousSelection = new DataTransfer();
      selectedFiles.forEach((file) => previousSelection.items.add(file));
      galleryImageInput.files = previousSelection.files;
      galleryImageError.textContent = "Hanggang 3 additional product photos lang ang puwede sa bawat produkto.";
      renderGalleryPreview([...existingImages, ...selectedFiles]);
      return;
    }
    selectedFiles = combinedFiles;
    const combinedSelection = new DataTransfer();
    selectedFiles.forEach((file) => combinedSelection.items.add(file));
    galleryImageInput.files = combinedSelection.files;
    galleryImageError.textContent = "";
    renderGalleryPreview([...existingImages, ...selectedFiles]);
  });
}

const productGrid = document.querySelector("#product-grid");
const viewButtons = document.querySelectorAll(".view-toggle-button");

if (productGrid && viewButtons.length) {
  viewButtons.forEach((button) => {
    button.addEventListener("click", () => {
      const isList = button.dataset.view === "list";
      productGrid.classList.toggle("is-list", isList);
      viewButtons.forEach((viewButton) => {
        const active = viewButton === button;
        viewButton.classList.toggle("is-active", active);
        viewButton.setAttribute("aria-pressed", String(active));
      });
    });
  });
}

document.querySelectorAll(".favorite-toggle").forEach((button) => {
  button.addEventListener("click", () => {
    const favorite = button.getAttribute("aria-pressed") !== "true";
    button.setAttribute("aria-pressed", String(favorite));
    button.setAttribute(
      "aria-label",
      favorite
        ? "Remove from favorites"
        : button.getAttribute("aria-label").replace("Remove from favorites", "Add to favorites"),
    );
    button.textContent = favorite ? "♥" : "♡";
  });
});

const quickViewDialog = document.querySelector("#quick-view-dialog");
const quickViewForm = document.querySelector("#quick-view-form");

if (quickViewDialog instanceof HTMLDialogElement) {
  const quickViewImage = document.querySelector("#quick-view-image");
  const quickViewCategory = document.querySelector("#quick-view-category");
  const quickViewTitle = document.querySelector("#quick-view-title");
  const quickViewPrice = document.querySelector("#quick-view-price");
  const quickViewDescription = document.querySelector("#quick-view-description");
  const quickViewStock = document.querySelector("#quick-view-stock");
  const quickViewThumbnails = document.querySelector("#quick-view-thumbnails");
  const quickViewSizes = document.querySelector("#quick-view-sizes");
  const quickViewColors = document.querySelector("#quick-view-colors");
  const quickViewReturnTo = document.querySelector("#quick-view-return-to");
  const quickViewQuantity = document.querySelector("#quick-view-quantity");
  const quickViewBuyNow = document.querySelector("#quick-view-buy-now");
  const quickViewRatingStars = document.querySelector("#quick-view-rating-stars");
  const quickViewRatingCount = document.querySelector("#quick-view-rating-count");
  const quickViewReviewsLink = document.querySelector("#quick-view-reviews-link");

  document.querySelectorAll(".quick-view-trigger").forEach((trigger) => {
    trigger.addEventListener("click", (event) => {
      event.preventDefault();
      const {
        productName,
        productCategory,
        productPrice,
        productDescription,
        productImage,
        productGallery,
        productColors,
        productStock,
        productRatingAverage,
        productRatingCount,
        productReviewPageUrl,
        cartUrl,
        returnUrl,
      } = trigger.dataset;
      if (!productName || !productCategory || !productPrice) return;
      if (quickViewForm instanceof HTMLFormElement && !cartUrl) return;

      const stock = Number(productStock) || 0;
      const gallery = [productImage, ...JSON.parse(productGallery || "[]")].filter(Boolean);
      const colors = JSON.parse(productColors || "[]");
      quickViewImage.src = gallery[0] || "";
      quickViewImage.alt = productName;
      quickViewThumbnails.replaceChildren();
      quickViewThumbnails.hidden = gallery.length < 2;
      gallery.forEach((image, index) => {
        const thumbnail = document.createElement("button");
        thumbnail.type = "button";
        thumbnail.className = `quick-view-thumbnail${index === 0 ? " is-active" : ""}`;
        thumbnail.setAttribute("aria-label", `View product image ${index + 1}`);
        thumbnail.setAttribute("aria-pressed", String(index === 0));
        const thumbnailImage = document.createElement("img");
        thumbnailImage.src = image;
        thumbnailImage.alt = "";
        thumbnailImage.loading = "lazy";
        thumbnail.append(thumbnailImage);
        thumbnail.addEventListener("click", () => {
          quickViewImage.src = image;
          quickViewThumbnails.querySelectorAll(".quick-view-thumbnail").forEach((item) => {
            const active = item === thumbnail;
            item.classList.toggle("is-active", active);
            item.setAttribute("aria-pressed", String(active));
          });
        });
        quickViewThumbnails.append(thumbnail);
      });
      quickViewCategory.textContent = productCategory;
      quickViewTitle.textContent = productName;
      quickViewPrice.textContent = `₱${Number(productPrice).toLocaleString("en-PH", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
      quickViewDescription.textContent = productDescription || "";
      quickViewStock.textContent = stock > 0 ? "● In Stock" : "Currently Sold Out";
      quickViewStock.classList.toggle("is-sold-out", stock < 1);
      if (quickViewForm instanceof HTMLFormElement) {
        quickViewForm.action = cartUrl;
        quickViewReturnTo.value = returnUrl || "/shop";
        quickViewReturnTo.dataset.shopUrl = returnUrl || "/shop";
        quickViewQuantity.value = "1";
        quickViewQuantity.max = String(Math.max(1, Math.min(stock, 10)));
        quickViewForm.querySelectorAll('input[name="size"]').forEach((input) => {
          input.checked = false;
          input.disabled = productCategory !== "T-Shirts" || stock < 1;
        });
        quickViewSizes.hidden = productCategory !== "T-Shirts";
        quickViewSizes.querySelector("#quick-view-size-selection").textContent = "";
        quickViewForm.querySelectorAll('input[name="color"]').forEach((input) => {
          input.checked = false;
          input.disabled = !colors.includes(input.value) || stock < 1;
        });
        quickViewColors.hidden = colors.length === 0;
        quickViewForm.querySelectorAll('button[type="submit"]').forEach((button) => {
          button.disabled = stock < 1;
        });
      }
      const averageRating = Number(productRatingAverage) || 0;
      const ratingCount = Number(productRatingCount) || 0;
      quickViewRatingStars.querySelectorAll("[data-rating-star]").forEach((star) => {
        star.classList.toggle("is-filled", Number(star.dataset.ratingStar) <= Math.round(averageRating));
      });
      quickViewRatingCount.textContent = ratingCount
        ? `${averageRating.toFixed(1)} / 5 (${ratingCount} ratings)`
        : "No ratings yet (0)";
      quickViewReviewsLink.href = productReviewPageUrl || "#";
      quickViewDialog.showModal();
    });
  });

  quickViewDialog.querySelector("[data-close-quick-view]")?.addEventListener("click", () => {
    quickViewDialog.close();
  });

  quickViewDialog.addEventListener("click", (event) => {
    if (event.target === quickViewDialog) quickViewDialog.close();
  });

  if (quickViewForm instanceof HTMLFormElement) {
    quickViewForm.addEventListener("submit", (event) => {
      quickViewReturnTo.value = event.submitter === quickViewBuyNow
        ? "/checkout"
        : quickViewReturnTo.dataset.shopUrl || "/shop";
    });
  }
}

const mainProductImage = document.querySelector("#main-product-image");

if (mainProductImage) {
  document.querySelectorAll(".product-thumbnail").forEach((thumbnail) => {
    thumbnail.addEventListener("click", () => {
      mainProductImage.src = thumbnail.dataset.image;
      document.querySelectorAll(".product-thumbnail").forEach((item) => {
        const active = item === thumbnail;
        item.classList.toggle("is-active", active);
        item.setAttribute("aria-pressed", String(active));
      });
    });
  });
}

document.querySelectorAll("[data-quantity-step]").forEach((button) => {
  button.addEventListener("click", () => {
    const input = button.closest(".quantity-control")?.querySelector("input");
    if (!(input instanceof HTMLInputElement)) return;
    const min = Number(input.min) || 1;
    const max = Number(input.max) || 10;
    const step = Number(button.dataset.quantityStep);
    input.value = String(Math.min(max, Math.max(min, Number(input.value) + step)));
  });
});

document.querySelectorAll('.size-picker input[name="size"]').forEach((input) => {
  input.addEventListener("change", () => {
    const label = input.closest(".size-picker")?.querySelector(".size-selection");
    if (label) label.textContent = `— ${input.value}`;
  });
});

document.querySelectorAll(".detail-wishlist").forEach((button) => {
  button.addEventListener("click", () => {
    const saved = button.getAttribute("aria-pressed") !== "true";
    button.setAttribute("aria-pressed", String(saved));
    button.innerHTML = saved ? "♥ &nbsp; Added to Wishlist" : "♡ &nbsp; Add to Wishlist";
  });
});
