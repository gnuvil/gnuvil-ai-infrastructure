(() => {
  const button = document.getElementById('theme-toggle');
  const labels = {system: '시스템 테마', light: '라이트 모드', dark: '다크 모드'};
  let theme = 'system';
  try { theme = localStorage.getItem('gnuvil-theme') || 'system'; } catch (_) {}
  if (!(theme in labels)) theme = 'system';
  function apply() {
    document.documentElement.dataset.theme = theme;
    button.textContent = labels[theme];
    try { localStorage.setItem('gnuvil-theme', theme); } catch (_) {}
  }
  apply();
  button.addEventListener('click', () => {
    theme = {system: 'light', light: 'dark', dark: 'system'}[theme];
    apply();
  });
  document.body.addEventListener('htmx:configRequest', event => {
    event.detail.headers['X-CSRF-Token'] = document.getElementById('dashboard').dataset.csrf;
  });
  function showRequestError(event) {
    const badge = document.getElementById('connection-status');
    badge.textContent = '갱신 지연';
    badge.className = 'connection delayed';
    for (const action of document.querySelectorAll('#dashboard form button')) {
      action.disabled = true;
    }
    const status = event.detail.xhr?.status;
    let message = '상태 갱신이 지연되고 있습니다. 자동으로 다시 확인합니다.';
    if (event.detail.requestConfig?.verb === 'post') {
      message = '제어 요청 결과를 확인할 수 없습니다. 서비스 상태를 다시 확인해 주세요.';
    }
    if (status === 401 || status === 403) {
      message = '인증을 확인하려면 페이지를 새로고침해 주세요.';
    }
    document.getElementById('refresh-status').textContent = message;
  }
  for (const event of ['htmx:responseError', 'htmx:sendError', 'htmx:timeout']) {
    document.body.addEventListener(event, showRequestError);
  }
})();
