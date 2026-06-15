# Перехват turnstile.render — метод 2captcha/RuCaptcha.
# Важно: вызываем оригинальный render, чтобы виджет работал при ручном/человеческом клике.
# Poll без таймаута — виджет на форме входа может загрузиться позже (SPA / iframe).
TURNSTILE_HOOK_SCRIPT = """
(function () {
    console.clear = function () { console.log('Console was cleared'); };

    window.__cfTryWrapTurnstile = function () {
        if (!window.turnstile || window.turnstile.__cfWrapped) {
            return !!(window.turnstile && window.turnstile.__cfWrapped);
        }
        var originalRender = window.turnstile.render.bind(window.turnstile);
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
                chlPageData: params.chlPageData || null,
                callback: params.callback || null,
                interceptTs: Date.now()
            };
            window.cfCallback = params.callback;
            return originalRender(container, params);
        };
        window.turnstile.__cfWrapped = true;
        return true;
    };

    window.__cfTryWrapTurnstile();
    if (!window.__cfTurnstilePollId) {
        window.__cfTurnstilePollId = setInterval(window.__cfTryWrapTurnstile, 10);
    }
})();
"""
