import '../sass/project.scss';

/* Project specific Javascript goes here. */
const { Popover, ScrollSpy, Modal } = window.bootstrap;

const popoverTriggerList = document.querySelectorAll(
  '[data-bs-toggle="popover"]',
);

const popoverList = [...popoverTriggerList].map(
  (popoverTriggerEl) => new Popover(popoverTriggerEl),
);

var scrollSpy = new ScrollSpy(document.body, {
  target: '#contents-list-group',
});

const isInteractiveElement = (element) =>
  !!element.closest(
    'a, button, input, select, textarea, label, [data-bs-toggle]',
  );

const openCardModal = (card) => {
  const trigger = card.querySelector('.js-review-modal-trigger');
  if (!trigger) return;

  trigger.click();
};

const openLinkedCard = (card) => {
  const href = card.getAttribute('data-card-href');
  if (!href) return;

  window.location.href = href;
};

document.addEventListener('click', (event) => {
  const card = event.target.closest('.js-review-card-modal');
  if (!card) return;

  if (isInteractiveElement(event.target)) return;

  openCardModal(card);
});

document.addEventListener('keydown', (event) => {
  const card = event.target.closest('.js-review-card-modal');
  if (!card) return;

  if (event.key !== 'Enter' && event.key !== ' ') return;

  event.preventDefault();
  openCardModal(card);
});

document.addEventListener('click', (event) => {
  const card = event.target.closest('.js-card-link');
  if (!card) return;

  if (isInteractiveElement(event.target)) return;

  openLinkedCard(card);
});

document.addEventListener('keydown', (event) => {
  const card = event.target.closest('.js-card-link');
  if (!card) return;

  if (event.key !== 'Enter' && event.key !== ' ') return;

  event.preventDefault();
  openLinkedCard(card);
});

let closingModalFromPopStateId = null;
let pendingModalTargetId = null;
let mobileDockDefaultMarkup = null;
let desktopDockDefaultMarkup = null;
let currentHomeModalIndex = -1;

const mobileDockSlot = () => document.getElementById('mobile-action-dock-slot');
const desktopDockSlot = () => document.getElementById('desktop-action-dock-slot');
const homeReviewModal = () => document.getElementById('home-review-modal');
const homeReviewModalContainer = () =>
  document.getElementById('home-review-modal-container');
const homeReviewModalPermalink = () =>
  document.getElementById('home-review-modal-permalink');

const getHomeReviewModalTriggers = () =>
  Array.from(
    document.querySelectorAll(
      '[data-review-modal-shell="home"][data-review-modal-url]',
    ),
  );

const setHomeReviewModalLoading = (isLoading) => {
  const modal = homeReviewModal();
  if (!modal) return;

  const controls = modal.querySelectorAll(
    '.btn-close, [data-bs-dismiss="modal"]',
  );
  controls.forEach((control) => {
    control.disabled = isLoading;
  });
};

const loadHomeReviewModalContent = async (trigger) => {
  const container = homeReviewModalContainer();
  if (!trigger || !container) return;

  setHomeReviewModalLoading(true);

  try {
    const response = await window.fetch(trigger.href, {
      headers: { 'HX-Request': 'true' },
      credentials: 'same-origin',
    });
    if (!response.ok) return;

    const html = await response.text();
    container.innerHTML = html;
  } finally {
    setHomeReviewModalLoading(false);
  }

  const permalink = homeReviewModalPermalink();
  if (permalink) {
    permalink.href = trigger.href;
    permalink.classList.remove('d-none');
  }
};

const rememberDefaultDockMarkup = () => {
  const mobileSlot = mobileDockSlot();
  const desktopSlot = desktopDockSlot();

  if (mobileSlot && mobileDockDefaultMarkup === null) {
    mobileDockDefaultMarkup = mobileSlot.innerHTML;
  }

  if (desktopSlot && desktopDockDefaultMarkup === null) {
    desktopDockDefaultMarkup = desktopSlot.innerHTML;
  }
};

const restoreDefaultDockMarkup = () => {
  const mobileSlot = mobileDockSlot();
  const desktopSlot = desktopDockSlot();

  if (mobileSlot && mobileDockDefaultMarkup !== null) {
    mobileSlot.innerHTML = mobileDockDefaultMarkup;
  }

  if (desktopSlot && desktopDockDefaultMarkup !== null) {
    desktopSlot.innerHTML = desktopDockDefaultMarkup;
  }
};

const syncModalDockMarkup = (modal) => {
  const template = document.querySelector(
    `[data-review-modal-dock-template="${modal.id}"]`,
  );
  if (!template) return;

  const markup = template.innerHTML.trim();
  const mobileSlot = mobileDockSlot();
  const desktopSlot = desktopDockSlot();

  if (mobileSlot) {
    mobileSlot.innerHTML = markup;
  }

  if (desktopSlot) {
    desktopSlot.innerHTML = markup;
  }
};

const getReviewModalIds = () =>
  Array.from(document.querySelectorAll('.modal[id^="modal-"]')).map(
    (modal) => modal.id,
  );

const updateReviewModalNavigator = (modal) => {
  if (modal.id === 'home-review-modal') {
    const triggers = getHomeReviewModalTriggers();
    const index = currentHomeModalIndex;
    if (index === -1) return;

    const prevButtons = document.querySelectorAll(
      `[data-current-modal-id="${modal.id}"][data-review-modal-nav="prev"]`,
    );
    const nextButtons = document.querySelectorAll(
      `[data-current-modal-id="${modal.id}"][data-review-modal-nav="next"]`,
    );
    const positions = document.querySelectorAll('[data-review-modal-position]');

    prevButtons.forEach((prevButton) => {
      prevButton.disabled = index <= 0;
      prevButton.dataset.targetIndex = String(index - 1);
      prevButton.dataset.currentModalId = modal.id;
    });

    nextButtons.forEach((nextButton) => {
      nextButton.disabled = index >= triggers.length - 1;
      nextButton.dataset.targetIndex = String(index + 1);
      nextButton.dataset.currentModalId = modal.id;
    });

    positions.forEach((position) => {
      position.textContent = `Review ${index + 1} of ${triggers.length}`;
    });

    return;
  }

  const modalIds = getReviewModalIds();
  const index = modalIds.indexOf(modal.id);
  if (index === -1) return;

  const prevButtons = document.querySelectorAll(
    `[data-current-modal-id="${modal.id}"][data-review-modal-nav="prev"]`,
  );
  const nextButtons = document.querySelectorAll(
    `[data-current-modal-id="${modal.id}"][data-review-modal-nav="next"]`,
  );
  const positions = document.querySelectorAll('[data-review-modal-position]');

  prevButtons.forEach((prevButton) => {
    prevButton.disabled = index <= 0;
    prevButton.dataset.targetModalId = modalIds[index - 1] || '';
    prevButton.dataset.currentModalId = modal.id;
  });

  nextButtons.forEach((nextButton) => {
    nextButton.disabled = index >= modalIds.length - 1;
    nextButton.dataset.targetModalId = modalIds[index + 1] || '';
    nextButton.dataset.currentModalId = modal.id;
  });

  positions.forEach((position) => {
    position.textContent = `Review ${index + 1} of ${modalIds.length}`;
  });
};

const cleanupModalArtifacts = () => {
  const anyOpenModal = document.querySelector('.modal.show');
  if (anyOpenModal) return;

  document.querySelectorAll('.modal-backdrop').forEach((el) => el.remove());
  document.body.classList.remove('modal-open');
  document.body.style.removeProperty('overflow');
  document.body.style.removeProperty('padding-right');
};

document.addEventListener('show.bs.modal', async (event) => {
  const modal = event.target;
  if (!modal.id || closingModalFromPopStateId) return;

  if (modal.id === 'home-review-modal') {
    const trigger = event.relatedTarget;
    const triggers = getHomeReviewModalTriggers();
    currentHomeModalIndex = trigger ? triggers.indexOf(trigger) : -1;
    await loadHomeReviewModalContent(trigger);
  }

  rememberDefaultDockMarkup();
  syncModalDockMarkup(modal);
  updateReviewModalNavigator(modal);

  if (!window.history.state || window.history.state.modalId !== modal.id) {
    window.history.pushState(
      { modalOpen: true, modalId: modal.id },
      '',
      window.location.href,
    );
  }
});

window.addEventListener('popstate', () => {
  const openModalEl = document.querySelector('.modal.show');
  if (!openModalEl) return;

  const modalInstance = Modal.getOrCreateInstance(openModalEl);
  closingModalFromPopStateId = openModalEl.id || null;
  modalInstance.hide();

  window.setTimeout(cleanupModalArtifacts, 400);
});

document.addEventListener('hidden.bs.modal', (event) => {
  const modal = event.target;
  if (!modal.id) {
    restoreDefaultDockMarkup();
    cleanupModalArtifacts();
    return;
  }

  if (closingModalFromPopStateId && closingModalFromPopStateId === modal.id) {
    closingModalFromPopStateId = null;
    if (modal.id === 'home-review-modal') {
      currentHomeModalIndex = -1;
    }
    restoreDefaultDockMarkup();
    cleanupModalArtifacts();
    return;
  }

  if (pendingModalTargetId) {
    const targetModalId = pendingModalTargetId;
    pendingModalTargetId = null;

    const trigger = document.querySelector(
      `[data-bs-target="#${targetModalId}"]`,
    );

    cleanupModalArtifacts();

    if (trigger) {
      window.setTimeout(() => {
        trigger.click();
      }, 0);
    }

    return;
  }

  const state = window.history.state;
  if (state && state.modalOpen && state.modalId === modal.id) {
    window.history.back();
  }

  if (modal.id === 'home-review-modal') {
    currentHomeModalIndex = -1;
    const permalink = homeReviewModalPermalink();
    if (permalink) {
      permalink.href = '#';
      permalink.classList.add('d-none');
    }
    const container = homeReviewModalContainer();
    if (container) {
      container.innerHTML = '';
    }
  }

  restoreDefaultDockMarkup();
  cleanupModalArtifacts();
});

rememberDefaultDockMarkup();

const getIssueReviewArticles = () =>
  Array.from(document.querySelectorAll('#articles .article-post[id^="list-item-"]'));

const getCurrentIssueReviewIndex = () => {
  const reviews = getIssueReviewArticles();
  if (!reviews.length) return -1;

  const offset = 140;
  const currentScroll = window.scrollY + offset;
  let currentIndex = 0;

  reviews.forEach((review, index) => {
    if (review.offsetTop <= currentScroll) {
      currentIndex = index;
    }
  });

  return currentIndex;
};

const updateIssueReviewNavigator = () => {
  const reviews = getIssueReviewArticles();
  const index = getCurrentIssueReviewIndex();
  if (!reviews.length || index === -1) return;

  const prevButtons = document.querySelectorAll('[data-issue-review-nav="prev"]');
  const nextButtons = document.querySelectorAll('[data-issue-review-nav="next"]');

  prevButtons.forEach((button) => {
    button.disabled = index <= 0;
    button.dataset.targetIndex = String(index - 1);
  });

  nextButtons.forEach((button) => {
    button.disabled = index >= reviews.length - 1;
    button.dataset.targetIndex = String(index + 1);
  });
};

document.addEventListener('click', async (event) => {
  const issueButton = event.target.closest('[data-issue-review-nav]');
  if (issueButton && !issueButton.disabled) {
    const targetIndex = Number(issueButton.dataset.targetIndex);
    const reviews = getIssueReviewArticles();
    const targetReview = reviews[targetIndex];
    if (targetReview) {
      targetReview.scrollIntoView({ behavior: 'smooth', block: 'start' });
      window.setTimeout(updateIssueReviewNavigator, 150);
    }
    return;
  }

  const button = event.target.closest('[data-review-modal-nav]');
  if (!button || button.disabled) return;

  const currentModalId = button.dataset.currentModalId;
  if (!currentModalId) return;

  if (currentModalId === 'home-review-modal') {
    const targetIndex = Number(button.dataset.targetIndex);
    const triggers = getHomeReviewModalTriggers();
    const targetTrigger = triggers[targetIndex];
    const modal = homeReviewModal();
    if (!targetTrigger || !modal) return;

    currentHomeModalIndex = targetIndex;
    await loadHomeReviewModalContent(targetTrigger);
    syncModalDockMarkup(modal);
    updateReviewModalNavigator(modal);
    return;
  }

  const targetModalId = button.dataset.targetModalId;
  if (!targetModalId) return;

  const trigger = document.querySelector(
    `[data-bs-target="#${targetModalId}"]`,
  );
  const currentModal = document.getElementById(currentModalId);
  if (!trigger || !currentModal) return;

  pendingModalTargetId = targetModalId;
  const currentInstance = Modal.getOrCreateInstance(currentModal);
  currentInstance.hide();
});

window.addEventListener('scroll', updateIssueReviewNavigator, { passive: true });
window.addEventListener('load', updateIssueReviewNavigator);
