(function () {
    function ensureContainer() {
        let container = document.getElementById("gamification-toast-container");
        if (container) {
            return container;
        }

        container = document.createElement("div");
        container.id = "gamification-toast-container";
        container.style.position = "fixed";
        container.style.top = "20px";
        container.style.right = "20px";
        container.style.zIndex = "2000";
        container.style.display = "grid";
        container.style.gap = "10px";
        container.style.maxWidth = "360px";
        document.body.appendChild(container);
        return container;
    }

    function buildToast(item) {
        const toast = document.createElement("div");
        toast.className = "gamification-toast";
        toast.style.padding = "14px 16px";
        toast.style.borderRadius = "16px";
        toast.style.background = "linear-gradient(135deg, rgba(18, 52, 84, 0.96), rgba(33, 102, 148, 0.95))";
        toast.style.color = "#fff";
        toast.style.boxShadow = "0 18px 34px rgba(11, 39, 66, 0.24)";
        toast.style.border = "1px solid rgba(170, 219, 248, 0.24)";
        toast.style.backdropFilter = "blur(10px)";
        toast.style.opacity = "0";
        toast.style.transform = "translateY(-8px)";
        toast.style.transition = "opacity 0.22s ease, transform 0.22s ease";

        const title = document.createElement("div");
        title.style.fontWeight = "800";
        title.style.marginBottom = item.detail ? "4px" : "0";
        title.textContent = item.label || "Progress updated";
        toast.appendChild(title);

        if (item.detail) {
            const detail = document.createElement("div");
            detail.style.fontSize = "0.9rem";
            detail.style.lineHeight = "1.45";
            detail.style.opacity = "0.88";
            detail.textContent = item.detail;
            toast.appendChild(detail);
        }

        return toast;
    }

    function show(items) {
        if (!Array.isArray(items) || items.length === 0) {
            return;
        }

        const container = ensureContainer();
        items.forEach(function (item, index) {
            const toast = buildToast(item || {});
            container.appendChild(toast);

            window.setTimeout(function () {
                toast.style.opacity = "1";
                toast.style.transform = "translateY(0)";
            }, 30 + (index * 90));

            window.setTimeout(function () {
                toast.style.opacity = "0";
                toast.style.transform = "translateY(-8px)";
                window.setTimeout(function () {
                    toast.remove();
                }, 220);
            }, 4200 + (index * 180));
        });
    }

    window.showGamificationFeedback = function (payload) {
        if (!payload) {
            return;
        }
        if (Array.isArray(payload)) {
            show(payload);
            return;
        }
        show(Array.isArray(payload.feedback_items) ? payload.feedback_items : []);
    };
})();
