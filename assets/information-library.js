/* Enhances static article links; fallback links remain available without JS. */
(() => {
 const form = document.querySelector('[data-information-filters]');
 if (form) {
  const query = form.querySelector('[name="query"]');
  const category = form.querySelector('[name="category"]');
  const grade = form.querySelector('[name="grade"]');
  const cards = [...document.querySelectorAll('[data-information-card]')];
  const groups = [...document.querySelectorAll('[data-information-group]')];
  const status = document.querySelector('[data-information-count]');
  const empty = document.querySelector('[data-information-empty]');
  const normalize = s => s.normalize('NFKC').toLowerCase().replace(/\s+/g, ' ').trim();
  const update = () => {
   const terms = normalize(query.value).split(' ').filter(Boolean);
   let count = 0;
   for (const card of cards) {
    const text = normalize(card.dataset.search);
    const show = terms.every(t => text.includes(t)) && (category.value === 'all' || card.dataset.category === category.value) && (grade.value === 'all' || card.dataset.grades.split(' ').includes(grade.value));
    card.hidden = !show;
    if (show) count++;
   }
   for (const group of groups) {
    const visible = [...group.querySelectorAll('[data-information-card]')].filter(c => !c.hidden).length;
    group.hidden = visible === 0;
    group.querySelector('[data-information-group-count]').textContent = `${visible}편`;
   }
   status.textContent = `${count}편의 글을 볼 수 있습니다.`;
   empty.hidden = count !== 0;
  };
  const reset = () => { query.value = ''; category.value = 'all'; grade.value = 'all'; update(); };
  form.addEventListener('submit', e => { e.preventDefault(); update(); });
  query.addEventListener('input', update);
  category.addEventListener('change', update);
  grade.addEventListener('change', update);
  form.addEventListener('reset', e => { e.preventDefault(); reset(); });
  document.querySelector('[data-reset-information]').addEventListener('click', reset);
  update();
 }
 const place = document.querySelector('[data-information-place]');
 if (place) {
  const region = place.querySelector('[name="region"]');
  const branch = place.querySelector('[name="branch"]');
  const status = place.querySelector('[data-place-status]');
  const output = place.querySelector('[data-place-links]');
  const grades = place.dataset.grades.split(' ');
  let places = [];
  const clearLinks = () => { output.replaceChildren(); };
  const addLink = (href, label, primary = false) => {
   const link = document.createElement('a');
   link.href = href; link.textContent = label + ' ↗'; link.className = primary ? 'il-button' : 'il-soft-button'; output.append(link);
  };
  const updateBranch = () => {
   clearLinks();
   const chosen = places.find(p => p.path === branch.value && p.region === region.value);
   if (!chosen) { status.textContent = region.value ? '지점을 선택하면 위치·수업과 동네 안내를 볼 수 있습니다.' : '지역을 먼저 선택해 주세요.'; return; }
   addLink(chosen.path, chosen.name + ' 지점 안내', true);
   const suitable = chosen.neighbors.filter(n => !n.grades.length || n.grades.some(g => grades.includes(g)));
   const neighbor = suitable[0] || chosen.neighbors[0];
   if (neighbor) addLink(neighbor.path, neighbor.label);
   else addLink('/전국학원/', '동네별 학원 안내 찾기');
   addLink(chosen.teacher, chosen.name + ' 선생님 소개');
   status.textContent = `${chosen.region} ${chosen.name}의 안내입니다. 개설 학년·과목과 현재 수업 조건은 지점에서 확인하세요.`;
  };
  const updateRegion = () => {
   branch.replaceChildren();
   const first = document.createElement('option'); first.value = ''; first.textContent = '지점을 선택하세요'; branch.append(first);
   const list = places.filter(p => p.region === region.value);
   for (const item of list) { const option = document.createElement('option'); option.value = item.path; option.textContent = item.name; branch.append(option); }
   branch.disabled = list.length === 0;
   branch.value = ''; updateBranch();
  };
  region.addEventListener('change', updateRegion);
  branch.addEventListener('change', updateBranch);
  fetch('/assets/information-places.json?v=20261004').then(response => {
   if (!response.ok) throw new Error('Unable to load location options');
   return response.json();
  }).then(data => { places = data.places; region.disabled = false; updateRegion(); }).catch(() => {
   status.textContent = '지역 목록을 불러오지 못했습니다. 아래 전국 지점안내와 동네별 안내 버튼에서 찾아주세요.';
   region.disabled = true; branch.disabled = true;
  });
 }
})();
