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
  document.body.addEventListener('htmx:responseError', () => {
    document.getElementById('refresh-status').textContent = '갱신에 실패했습니다. 인증 상태를 확인하거나 페이지를 새로고침해 주세요.';
  });
  document.body.addEventListener('htmx:sendError', () => {
    document.getElementById('refresh-status').textContent = '연결이 끊겼습니다. 표시된 상태가 최신이 아닐 수 있습니다. 자동으로 다시 확인합니다.';
  });
})();
