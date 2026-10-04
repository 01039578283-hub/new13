/* Progressive enhancement: every branch remains reachable without JavaScript. */
(() => {
  const form = document.querySelector('[data-teacher-filters]');
  if (!form) return;
  const query = form.querySelector('[name="query"]');
  const region = form.querySelector('[name="region"]');
  const theme = form.querySelector('[name="theme"]');
  const count = document.querySelector('[data-teacher-count]');
  const empty = document.querySelector('[data-teacher-empty]');
  const groups = Array.from(document.querySelectorAll('[data-teacher-group]'));
  const cards = Array.from(document.querySelectorAll('[data-teacher-card]'));
  const normal = value => String(value).normalize('NFKC').toLocaleLowerCase('ko-KR');
  const records = cards.map(card => ({
    card,
    text: normal(card.dataset.search),
    region: card.dataset.region,
    themes: JSON.parse(card.dataset.themes),
    profiles: Number(card.dataset.profiles)
  }));
  function apply() {
    const words = normal(query.value.trim()).split(/\s+/).filter(Boolean);
    let branches = 0;
    let profiles = 0;
    records.forEach(record => {
      const match = words.every(word => record.text.includes(word)) &&
        (region.value === 'all' || record.region === region.value) &&
        (theme.value === 'all' || record.themes.includes(theme.value));
      record.card.hidden = !match;
      if (match) { branches += 1; profiles += record.profiles; }
    });
    groups.forEach(group => {
      const visible = Array.from(group.querySelectorAll('[data-teacher-card]')).filter(card => !card.hidden);
      group.hidden = visible.length === 0;
      const groupCount = group.querySelector('[data-teacher-group-count]');
      if (groupCount) groupCount.textContent = `${visible.length}개 지점`;
    });
    count.textContent = branches
      ? `${branches.toLocaleString('ko-KR')}개 지점의 소개 ${profiles.toLocaleString('ko-KR')}건을 볼 수 있습니다. 조건은 지점 단위로 적용됩니다.`
      : '조건에 맞는 지점이 없습니다.';
    empty.hidden = branches !== 0;
  }
  function clear() {
    query.value = '';
    region.value = form.dataset.defaultRegion || 'all';
    theme.value = 'all';
    apply();
  }
  form.addEventListener('submit', event => event.preventDefault());
  form.addEventListener('input', apply);
  form.addEventListener('change', apply);
  form.addEventListener('reset', event => { event.preventDefault(); clear(); });
  document.querySelectorAll('[data-reset-teachers]').forEach(button => button.addEventListener('click', () => { clear(); query.focus(); }));
  apply();
})();
