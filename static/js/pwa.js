let deferredPrompt = null;

function isIosDevice() {
    return /iphone|ipad|ipod/i.test(window.navigator.userAgent);
}

function isSafariBrowser() {
    const ua = window.navigator.userAgent;
    return /safari/i.test(ua) && !/crios|fxios|edgios|android/i.test(ua);
}

function isStandaloneMode() {
    return window.matchMedia("(display-mode: standalone)").matches || window.navigator.standalone === true;
}

function dismissedBanner() {
    try {
        return window.localStorage.getItem("timetable-pwa-banner-dismissed") === "1";
    } catch (error) {
        return false;
    }
}

function setBannerDismissed() {
    try {
        window.localStorage.setItem("timetable-pwa-banner-dismissed", "1");
    } catch (error) {
        return;
    }
}

function renderBanner() {
    const banner = document.querySelector("[data-pwa-banner]");
    if (!banner) {
        return;
    }

    const message = banner.querySelector("[data-pwa-message]");
    const installButton = banner.querySelector("[data-pwa-install]");

    if (!message || !installButton || isStandaloneMode() || dismissedBanner()) {
        banner.hidden = true;
        return;
    }

    if (deferredPrompt) {
        message.textContent = "Install this timetable app on your phone for quick full-screen access.";
        installButton.hidden = false;
        banner.hidden = false;
        return;
    }

    if (isIosDevice() && isSafariBrowser()) {
        message.textContent = "In Safari, tap Share and choose Add to Home Screen to install this app on iPhone.";
        installButton.hidden = true;
        banner.hidden = false;
        return;
    }

    banner.hidden = true;
}

async function promptInstall() {
    if (!deferredPrompt) {
        return;
    }

    deferredPrompt.prompt();
    await deferredPrompt.userChoice;
    deferredPrompt = null;
    renderBanner();
}

function registerServiceWorker() {
    if (!("serviceWorker" in window.navigator)) {
        return;
    }

    window.addEventListener("load", function () {
        window.navigator.serviceWorker.register("/service-worker.js").catch(function () {
            return;
        });
    });
}

document.addEventListener("DOMContentLoaded", function () {
    registerServiceWorker();

    const banner = document.querySelector("[data-pwa-banner]");
    if (!banner) {
        return;
    }

    const installButton = banner.querySelector("[data-pwa-install]");
    const dismissButton = banner.querySelector("[data-pwa-dismiss]");

    if (installButton) {
        installButton.addEventListener("click", promptInstall);
    }

    if (dismissButton) {
        dismissButton.addEventListener("click", function () {
            setBannerDismissed();
            banner.hidden = true;
        });
    }

    renderBanner();
});

window.addEventListener("beforeinstallprompt", function (event) {
    event.preventDefault();
    deferredPrompt = event;
    renderBanner();
});

window.addEventListener("appinstalled", function () {
    deferredPrompt = null;
    setBannerDismissed();
    renderBanner();
});
