# Перехват turnstile.render — метод 2captcha/RuCaptcha.
# Без Object.defineProperty (ломает инициализацию Cloudflare).
# Оригинальный render НЕ вызываем.
TURNSTILE_HOOK_SCRIPT = """
(function () {
    if (window.__cfTurnstileHookInstalled) return;
    window.__cfTurnstileHookInstalled = true;
    console.clear = function () { console.log('Console was cleared'); };

    var poll = setInterval(function () {
        if (!window.turnstile || window.turnstile.__cfWrapped) return;
        clearInterval(poll);
        window.turnstile.render = function (container, params) {
            var payload = {
                sitekey: params.sitekey,
                pageurl: window.location.href,
                data: params.cData,
                pagedata: params.chlPageData,
                action: params.action,
                userAgent: navigator.userAgent
            };
            console.log('intercepted-params:' + JSON.stringify(payload));
            window.__cfTurnstileParams = {
                sitekey: params.sitekey,
                action: params.action || null,
                cData: params.cData || null,
                chlPageData: params.chlPageData || null
            };
            window.cfCallback = params.callback;
            return 'cf-intercepted-' + Date.now();
        };
        window.turnstile.__cfWrapped = true;
    }, 10);
    setTimeout(function () { clearInterval(poll); }, 120000);
})();
"""
