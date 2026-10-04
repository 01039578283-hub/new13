(() => {
  'use strict';
  const filterForm = document.querySelector('[data-guide-filters]');
  if (filterForm) {
    const search = document.querySelector('#guide-search');
    const grade = document.querySelector('#guide-grade');
    const category = document.querySelector('#guide-category');
    const cards = [...document.querySelectorAll('[data-guide-card]')];
    const groups = [...document.querySelectorAll('[data-guide-group]')];
    const normalize = text => text.normalize('NFKC').toLocaleLowerCase('ko').replace(/\s+/g, ' ').trim();
    const apply = () => {
      const terms = normalize(search.value).split(' ').filter(Boolean);
      let count = 0;
      for (const card of cards) {
        const matches = (grade.value === 'all' || card.dataset.grades.split(' ').includes(grade.value)) &&
          (category.value === 'all' || card.dataset.category === category.value) &&
          terms.every(term => normalize(card.dataset.search).includes(term));
        card.hidden = !matches;
        if (matches) count++;
      }
      for (const group of groups) {
        const visible = [...group.querySelectorAll('[data-guide-card]')].filter(card => !card.hidden).length;
        group.hidden = visible === 0;
        group.querySelector('[data-group-count]').textContent = `${visible}편`;
      }
      document.querySelector('[data-guide-count]').textContent = `조건에 맞는 가이드 ${count}편을 볼 수 있습니다.`;
      document.querySelector('[data-guide-empty]').hidden = count !== 0;
    };
    filterForm.addEventListener('submit', event => event.preventDefault());
    filterForm.addEventListener('input', apply);
    filterForm.addEventListener('change', apply);
    const resetFilters = () => {
      search.value = '';
      grade.value = 'all';
      category.value = 'all';
      apply();
    };
    filterForm.addEventListener('reset', event => { event.preventDefault(); resetFilters(); });
    document.querySelector('[data-reset-filters]').addEventListener('click', () => { resetFilters(); search.focus(); });
  }
  for (const button of document.querySelectorAll('[data-print]')) {
    button.addEventListener('click', () => window.print());
  }
  const record = document.querySelector('[data-practice-record]');
  if (record) {
    record.addEventListener('submit', event => event.preventDefault());
    document.querySelector('[data-download-record]').addEventListener('click', () => {
      const title = document.querySelector('h1').textContent.trim();
      const lines = [title, `기록한 날짜: ${new Date().toLocaleDateString('ko-KR')}`, ''];
      for (const label of record.querySelectorAll('fieldset label')) {
        lines.push(`${label.querySelector('input').checked ? '[v]' : '[ ]'} ${label.textContent.trim()}`);
      }
      for (const textarea of record.querySelectorAll('textarea')) {
        const label = record.querySelector(`label[for="${textarea.id}"]`).textContent;
        lines.push('', label, textarea.value.trim() || '(아직 작성하지 않음)');
      }
      const blob = new Blob(['\uFEFF', lines.join('\n').replace(/\r\n|\r|\n/g, '\r\n')], { type: 'text/plain;charset=utf-8' });
      const objectUrl = URL.createObjectURL(blob);
      const anchor = document.createElement('a');
      anchor.href = objectUrl;
      anchor.download = `${title.replace(/[\\/:*?"<>|]/g, '-')}-실천기록.txt`;
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      setTimeout(() => URL.revokeObjectURL(objectUrl), 1000);
      record.querySelector('[data-record-status]').textContent = 'TXT 기록의 내려받기를 요청했습니다. 브라우저의 다운로드 목록에서 확인하세요.';
    });
  }
  // Reveal solutions and fit record fields for paper, then restore the reading state.
  let printDetails = [];
  let printFields = [];
  window.addEventListener('beforeprint', () => {
    printDetails = [...document.querySelectorAll('details')].map(node => [node, node.open]);
    printDetails.forEach(([node]) => { node.open = true; });
    printFields = [...document.querySelectorAll('[data-practice-record] textarea')].map(node => [node, node.style.height]);
    printFields.forEach(([node]) => { node.style.height = `${Math.max(90, node.scrollHeight + 4)}px`; });
  });
  window.addEventListener('afterprint', () => {
    printDetails.forEach(([node, open]) => { node.open = open; });
    printFields.forEach(([node, height]) => { node.style.height = height; });
  });
})();
