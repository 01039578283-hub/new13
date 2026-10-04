(() => {
  const form = document.querySelector('[data-curriculum-filter]');
  if (!form) return;
  const query = form.elements.query, grade = form.elements.grade, subject = form.elements.subject;
  const cards = [...document.querySelectorAll('[data-curriculum-card]')];
  const groups = [...document.querySelectorAll('[data-curriculum-group]')];
  const count = document.querySelector('[data-curriculum-count]');
  const empty = document.querySelector('[data-curriculum-empty]');
  const normalize = value => value.toLocaleLowerCase('ko-KR').replace(/\s+/g, '');
  function update() {
    const terms = normalize(query.value).split(/[,，]/).filter(Boolean);
    let visible = 0;
    for (const card of cards) {
      const text = normalize(card.dataset.search);
      card.hidden = !(grade.value === 'all' || card.dataset.grade === grade.value) ||
        !(subject.value === 'all' || card.dataset.subject === subject.value) || !terms.every(term => text.includes(term));
      if (!card.hidden) visible++;
    }
    for (const group of groups) group.hidden = ![...group.querySelectorAll('[data-curriculum-card]')].some(card => !card.hidden);
    count.textContent = `${visible}개의 학년·과목 안내를 볼 수 있습니다.`;
    empty.hidden = visible !== 0;
  }
  const params = new URLSearchParams(window.location.search);
  for (const [key, control] of [['grade', grade], ['subject', subject]]) {
    const value = params.get(key);
    if (value && [...control.options].some(option => option.value === value)) control.value = value;
  }
  if (params.has('query')) query.value = params.get('query');
  form.addEventListener('input', update);
  form.addEventListener('change', update);
  form.addEventListener('submit', event => event.preventDefault());
  form.addEventListener('reset', () => setTimeout(update, 0));
  update();
})();
