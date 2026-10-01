(function () {
    "use strict";

    var canvas = document.getElementById("atmosphere-canvas");
    if (!canvas) return;

    var ctx = canvas.getContext("2d", { alpha: true });
    if (!ctx) return;

    var reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
    var isMobile = window.matchMedia("(max-width: 640px)");
    var running = false;
    var rafId = 0;
    var width = 0;
    var height = 0;
    var dpr = 1;
    var sparks = [];
    var orbs = [];
    var start = performance.now();

    function random(min, max) {
        return min + Math.random() * (max - min);
    }

    function setupScene() {
        var count = isMobile.matches ? 18 : 28;
        sparks = [];
        for (var i = 0; i < count; i++) {
            sparks.push({
                x: Math.random(),
                y: Math.random(),
                r: random(0.6, 1.8),
                speed: random(0.012, 0.035),
                drift: random(-0.008, 0.008),
                phase: random(0, Math.PI * 2),
                alpha: random(0.12, 0.38)
            });
        }

        orbs = [
            { x: 0.18, y: 0.22, r: 0.28, hue: 230, dx: 0.018, dy: 0.012, phase: 0.4 },
            { x: 0.82, y: 0.18, r: 0.24, hue: 275, dx: -0.014, dy: 0.016, phase: 1.6 },
            { x: 0.72, y: 0.78, r: 0.32, hue: 250, dx: -0.01, dy: -0.012, phase: 2.8 },
            { x: 0.22, y: 0.74, r: 0.22, hue: 210, dx: 0.012, dy: -0.01, phase: 3.9 }
        ];
    }

    function resize() {
        dpr = Math.min(window.devicePixelRatio || 1, isMobile.matches ? 1.25 : 1.5);
        width = window.innerWidth;
        height = window.innerHeight;
        canvas.width = Math.floor(width * dpr);
        canvas.height = Math.floor(height * dpr);
        canvas.style.width = width + "px";
        canvas.style.height = height + "px";
        ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    }

    function sparklineY(xNorm, t, band) {
        var pulse = 0.5 + 0.5 * Math.sin(t * 1.15);
        var wave =
            Math.sin(xNorm * Math.PI * 2.4 + t * 0.55) * 0.028 +
            Math.sin(xNorm * Math.PI * 5.2 - t * 0.9) * 0.012 +
            pulse * 0.02 * Math.sin(xNorm * Math.PI);
        var rise = xNorm * 0.035;
        if (band === "top") return height * (0.08 - wave - rise);
        return height * (0.92 - wave - rise);
    }

    function drawOrbs(t) {
        for (var i = 0; i < orbs.length; i++) {
            var o = orbs[i];
            var x = (o.x + Math.sin(t * o.dx + o.phase) * 0.06) * width;
            var y = (o.y + Math.cos(t * o.dy + o.phase) * 0.05) * height;
            var radius = o.r * Math.min(width, height);
            var g = ctx.createRadialGradient(x, y, 0, x, y, radius);
            g.addColorStop(0, "hsla(" + o.hue + ", 82%, 78%, 0.28)");
            g.addColorStop(0.45, "hsla(" + o.hue + ", 70%, 62%, 0.12)");
            g.addColorStop(1, "hsla(" + o.hue + ", 70%, 50%, 0)");
            ctx.fillStyle = g;
            ctx.beginPath();
            ctx.arc(x, y, radius, 0, Math.PI * 2);
            ctx.fill();
        }
    }

    function drawPulseRings(t) {
        var cx = width * 0.5;
        var cy = height * 0.5;
        var beat = (t * 0.18) % 1;
        for (var i = 0; i < 3; i++) {
            var p = (beat + i / 3) % 1;
            var radius = Math.min(width, height) * (0.22 + p * 0.48);
            ctx.beginPath();
            ctx.arc(cx, cy, radius, 0, Math.PI * 2);
            ctx.strokeStyle = "rgba(255,255,255," + (0.2 * (1 - p)) + ")";
            ctx.lineWidth = 1.4;
            ctx.stroke();
        }
    }

    function drawOneSparkline(t, band, headOffset) {
        var steps = isMobile.matches ? 48 : 80;
        ctx.beginPath();
        for (var i = 0; i <= steps; i++) {
            var xNorm = i / steps;
            var x = xNorm * width;
            var y = sparklineY(xNorm, t, band);
            if (i === 0) ctx.moveTo(x, y);
            else ctx.lineTo(x, y);
        }
        ctx.shadowColor = "rgba(255,255,255,0.55)";
        ctx.shadowBlur = 12;
        ctx.strokeStyle = "rgba(255,255,255,0.62)";
        ctx.lineWidth = 2.4;
        ctx.lineJoin = "round";
        ctx.stroke();
        ctx.shadowBlur = 0;

        var glow = ctx.createLinearGradient(0, 0, width, 0);
        glow.addColorStop(0, "rgba(224,231,255,0)");
        glow.addColorStop(0.5, "rgba(255,255,255,0.28)");
        glow.addColorStop(1, "rgba(224,231,255,0)");
        ctx.strokeStyle = glow;
        ctx.lineWidth = 7;
        ctx.stroke();

        var head = (t * 0.09 + headOffset) % 1;
        var hx = head * width;
        var hy = sparklineY(head, t, band);
        var hg = ctx.createRadialGradient(hx, hy, 0, hx, hy, 32);
        hg.addColorStop(0, "rgba(255,255,255,0.7)");
        hg.addColorStop(0.35, "rgba(196,181,253,0.32)");
        hg.addColorStop(1, "rgba(196,181,253,0)");
        ctx.fillStyle = hg;
        ctx.beginPath();
        ctx.arc(hx, hy, 32, 0, Math.PI * 2);
        ctx.fill();
    }

    function drawSparkline(t) {
        drawOneSparkline(t, "top", 0);
        drawOneSparkline(t, "bottom", 0.45);
    }

    function drawSparks(t) {
        for (var i = 0; i < sparks.length; i++) {
            var s = sparks[i];
            s.y -= s.speed * 0.004;
            s.x += s.drift * 0.004;
            if (s.y < -0.04) {
                s.y = 1.04;
                s.x = Math.random();
            }
            if (s.x < -0.04) s.x = 1.04;
            if (s.x > 1.04) s.x = -0.04;
            var twinkle = 0.55 + 0.45 * Math.sin(t * 2.2 + s.phase);
            ctx.beginPath();
            ctx.arc(s.x * width, s.y * height, s.r, 0, Math.PI * 2);
            ctx.fillStyle = "rgba(255,255,255," + (s.alpha * twinkle) + ")";
            ctx.fill();
        }
    }

    function paint(now) {
        var t = (now - start) / 1000;
        ctx.clearRect(0, 0, width, height);
        drawOrbs(t);
        drawPulseRings(t);
        drawSparkline(t);
        drawSparks(t);
    }

    function frame(now) {
        if (!running) return;
        paint(now);
        rafId = requestAnimationFrame(frame);
    }

    function startLoop() {
        if (running || reducedMotion.matches || document.hidden) return;
        running = true;
        rafId = requestAnimationFrame(frame);
    }

    function stopLoop() {
        running = false;
        if (rafId) cancelAnimationFrame(rafId);
        rafId = 0;
    }

    function paintStatic() {
        resize();
        paint(start);
    }

    function syncMotion() {
        stopLoop();
        resize();
        if (reducedMotion.matches) {
            document.documentElement.classList.add("reduced-atmosphere");
            paintStatic();
            return;
        }
        document.documentElement.classList.remove("reduced-atmosphere");
        if (!document.hidden) startLoop();
        else paintStatic();
    }

    setupScene();
    resize();
    window.addEventListener("resize", function () {
        setupScene();
        resize();
        if (!running) paintStatic();
    }, { passive: true });
    document.addEventListener("visibilitychange", function () {
        if (document.hidden) {
            stopLoop();
        } else {
            syncMotion();
        }
    });
    if (typeof reducedMotion.addEventListener === "function") {
        reducedMotion.addEventListener("change", syncMotion);
    } else if (typeof reducedMotion.addListener === "function") {
        reducedMotion.addListener(syncMotion);
    }
    syncMotion();
})();
