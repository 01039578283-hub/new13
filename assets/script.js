document.addEventListener("DOMContentLoaded", () => {
  const current = decodeURI(location.href).replace(/\/index\.html$/, "/");
  document.querySelectorAll(".nav a").forEach((link) => {
    const href = decodeURI(new URL(link.getAttribute("href"), location.href).href).replace(/\/index\.html$/, "/");
    const isHome = link.textContent.trim() === "홈";
    const isActive = isHome ? current === href : (current === href || current.startsWith(href));
    if (isActive) {
      link.classList.add("is-active");
    }
  });

  const filterButtons = Array.from(document.querySelectorAll("[data-edu-filter]"));
  const guideCards = Array.from(document.querySelectorAll("[data-edu-category]"));
  const guideCount = document.querySelector("[data-edu-count]");
  const emptyState = document.querySelector("[data-edu-empty]");

  if (filterButtons.length && guideCards.length) {
    const applyFilter = (filter) => {
      let visibleCount = 0;
      guideCards.forEach((card) => {
        const isVisible = filter === "all" || card.dataset.eduCategory === filter;
        card.hidden = !isVisible;
        if (isVisible) visibleCount += 1;
      });
      filterButtons.forEach((button) => {
        const isActive = button.dataset.eduFilter === filter;
        button.classList.toggle("is-active", isActive);
        button.setAttribute("aria-pressed", String(isActive));
      });
      if (guideCount) guideCount.textContent = String(visibleCount);
      if (emptyState) emptyState.hidden = visibleCount !== 0;
    };

    filterButtons.forEach((button) => {
      button.addEventListener("click", () => applyFilter(button.dataset.eduFilter));
    });
    applyFilter("all");
  }

  document.querySelectorAll("[data-academy-directory]").forEach((directory) => {
    const input = directory.querySelector("[data-academy-search]");
    const clearButton = directory.querySelector("[data-academy-search-clear]");
    const status = directory.querySelector("[data-academy-search-status]");
    const page = directory.closest("section") || document;
    const regions = Array.from(page.querySelectorAll("[data-academy-region]"));
    const shortcutButtons = Array.from(directory.querySelectorAll("[data-academy-region-target]"));
    const normalize = (value) => value.toLocaleLowerCase("ko-KR").replace(/\s+/g, "");

    const setActiveRegion = (name) => {
      shortcutButtons.forEach((button) => {
        button.setAttribute("aria-pressed", String(button.dataset.academyRegionTarget === name));
      });
    };

    const resetDirectory = () => {
      regions.forEach((region, index) => {
        region.hidden = false;
        region.open = index === 0;
        region.querySelectorAll(".academy-city-group, .academy-link-grid a").forEach((item) => {
          item.hidden = false;
        });
      });
      setActiveRegion(regions[0]?.dataset.academyRegion || "");
      if (status) status.textContent = "광역지역을 열거나 동네 이름을 검색하세요.";
      if (clearButton) clearButton.hidden = true;
    };

    const applySearch = () => {
      const rawQuery = input?.value.trim() || "";
      const query = normalize(rawQuery);
      if (!query) {
        resetDirectory();
        return;
      }

      let resultCount = 0;
      regions.forEach((region) => {
        let regionCount = 0;
        region.querySelectorAll(".academy-city-group").forEach((city) => {
          let cityCount = 0;
          city.querySelectorAll(".academy-link-grid a").forEach((link) => {
            const matches = normalize(link.textContent).includes(query);
            link.hidden = !matches;
            if (matches) {
              cityCount += 1;
              regionCount += 1;
              resultCount += 1;
            }
          });
          city.hidden = cityCount === 0;
        });
        region.hidden = regionCount === 0;
        region.open = regionCount > 0;
      });

      setActiveRegion("");
      if (clearButton) clearButton.hidden = false;
      if (status) {
        status.textContent = resultCount
          ? `‘${rawQuery}’ 검색 결과 ${resultCount}개의 동네 페이지를 찾았습니다.`
          : `‘${rawQuery}’에 해당하는 동네가 없습니다.`;
      }
    };

    input?.addEventListener("input", applySearch);
    clearButton?.addEventListener("click", () => {
      input.value = "";
      resetDirectory();
      input.focus();
    });

    shortcutButtons.forEach((button) => {
      button.addEventListener("click", () => {
        if (input) input.value = "";
        resetDirectory();
        const targetName = button.dataset.academyRegionTarget;
        const target = regions.find((region) => region.dataset.academyRegion === targetName);
        if (!target) return;
        regions.forEach((region) => { region.open = region === target; });
        setActiveRegion(targetName);
        target.scrollIntoView({ behavior: "smooth", block: "start" });
      });
    });

    regions.forEach((region) => {
      region.addEventListener("toggle", () => {
        if (region.open && !input?.value.trim()) setActiveRegion(region.dataset.academyRegion);
      });
    });
  });

  const carousel = document.querySelector("[data-library-carousel]");
  if (!carousel) return;

  const track = carousel.querySelector(".library-track");
  const slides = Array.from(carousel.querySelectorAll(".library-slide"));
  const dots = Array.from(carousel.querySelectorAll("[data-library-dot]"));
  const prev = document.querySelector("[data-library-prev]");
  const next = document.querySelector("[data-library-next]");
  const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
  let currentIndex = 0;
  let timer = null;

  const goToSlide = (nextIndex) => {
    currentIndex = (nextIndex + slides.length) % slides.length;
    track.style.transform = `translateX(-${currentIndex * 100}%)`;
    dots.forEach((dot, index) => {
      dot.classList.toggle("is-active", index === currentIndex);
      dot.setAttribute("aria-current", index === currentIndex ? "true" : "false");
    });
    slides.forEach((slide, index) => {
      const isCurrent = index === currentIndex;
      slide.setAttribute("aria-hidden", String(!isCurrent));
      slide.querySelectorAll("a, button").forEach((control) => {
        if (isCurrent) control.removeAttribute("tabindex");
        else control.setAttribute("tabindex", "-1");
      });
    });
  };

  const stopAuto = () => {
    if (timer) window.clearInterval(timer);
  };

  const startAuto = () => {
    stopAuto();
    if (reduceMotion.matches || document.hidden) return;
    timer = window.setInterval(() => goToSlide(currentIndex + 1), 4200);
  };

  prev?.addEventListener("click", () => {
    goToSlide(currentIndex - 1);
    startAuto();
  });

  next?.addEventListener("click", () => {
    goToSlide(currentIndex + 1);
    startAuto();
  });

  dots.forEach((dot) => {
    dot.addEventListener("click", () => {
      goToSlide(Number(dot.dataset.libraryDot));
      startAuto();
    });
  });

  carousel.addEventListener("mouseenter", stopAuto);
  carousel.addEventListener("mouseleave", startAuto);
  carousel.addEventListener("focusin", stopAuto);
  carousel.addEventListener("focusout", startAuto);
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) stopAuto();
    else startAuto();
  });
  reduceMotion.addEventListener?.("change", startAuto);

  goToSlide(0);
  startAuto();
});
