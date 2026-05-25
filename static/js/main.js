// ─────────────────────────────────────────────────────────────────────────────
// Bu Nurbek — UI behaviors
// ─────────────────────────────────────────────────────────────────────────────

// 1. Nav scroll effect (frosted glass on scroll)
const nav = document.querySelector('.nav');
const mobileCta = document.querySelector('.mobile-cta');
const hero = document.querySelector('.hero');

function onScroll() {
  const y = window.scrollY;
  nav?.classList.toggle('scrolled', y > 40);

  // Show mobile sticky CTA only AFTER user scrolls past hero
  if (mobileCta && hero) {
    const heroBottom = hero.offsetHeight - 120;
    mobileCta.classList.toggle('mobile-cta--visible', y > heroBottom);
  }
}
window.addEventListener('scroll', onScroll, { passive: true });
onScroll();

// 2. Scroll-triggered fade-in animations using IntersectionObserver
if ('IntersectionObserver' in window) {
  const observer = new IntersectionObserver((entries) => {
    entries.forEach(entry => {
      if (entry.isIntersecting) {
        entry.target.classList.add('in');
        observer.unobserve(entry.target);
      }
    });
  }, {
    threshold: 0.12,
    rootMargin: '0px 0px -80px 0px',
  });
  document.querySelectorAll('.fade-in, .scale-in').forEach(el => observer.observe(el));
}

// 3. Counter animation — when a [data-count] element scrolls into view
function animateCounter(el, target, duration = 1400) {
  const isFloat = target % 1 !== 0;
  const suffix = el.dataset.suffix || '';
  const start = performance.now();
  function step(now) {
    const t = Math.min((now - start) / duration, 1);
    const eased = 1 - Math.pow(1 - t, 3); // ease-out-cubic
    const val = isFloat ? (target * eased).toFixed(1) : Math.floor(target * eased);
    el.textContent = val + suffix;
    if (t < 1) requestAnimationFrame(step);
  }
  requestAnimationFrame(step);
}

if ('IntersectionObserver' in window) {
  const counterObs = new IntersectionObserver((entries) => {
    entries.forEach(entry => {
      if (entry.isIntersecting) {
        const target = parseFloat(entry.target.dataset.count);
        if (!isNaN(target)) animateCounter(entry.target, target);
        counterObs.unobserve(entry.target);
      }
    });
  }, { threshold: 0.5 });
  document.querySelectorAll('[data-count]').forEach(el => counterObs.observe(el));
}

// 4. Smooth scroll for in-page anchors (already works via CSS, but offset for nav)
document.querySelectorAll('a[href^="#"]').forEach(link => {
  link.addEventListener('click', (e) => {
    const id = link.getAttribute('href');
    if (id === '#' || id.length < 2) return;
    const target = document.querySelector(id);
    if (!target) return;
    e.preventDefault();
    const navH = nav?.offsetHeight || 0;
    const y = target.getBoundingClientRect().top + window.scrollY - navH - 16;
    window.scrollTo({ top: y, behavior: 'smooth' });
  });
});

// 5. Auto-dismiss flash messages
document.querySelectorAll('.message').forEach(el => {
  setTimeout(() => {
    el.style.transition = 'opacity .3s, transform .3s';
    el.style.opacity = '0';
    el.style.transform = 'translateX(20px)';
    setTimeout(() => el.remove(), 300);
  }, 4000);
});
