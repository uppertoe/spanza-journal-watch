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
const sharedReviewModalIndices = new Map();

const mobileDockSlot = () => document.getElementById('mobile-action-dock-slot');
const desktopDockSlot = () => document.getElementById('desktop-action-dock-slot');
const mobileToolbarInner = () =>
  document.querySelector('.sticky-mobile-toolbar__inner');
const getSharedReviewModalTriggers = (modalId) =>
  Array.from(
    document.querySelectorAll(
      `[data-bs-target="#${modalId}"][data-review-modal-url]`,
    ),
  );

const getSharedReviewModalContainer = (modal) =>
  modal?.querySelector('[data-shared-review-modal-container]');

const getModalShareMetadata = (modal) =>
  modal?.querySelector('[data-share-metadata]');

const toAbsoluteUrl = (url) => {
  if (!url) return '';
  return new URL(url, window.location.origin).toString();
};

const copyTextToClipboard = async (text) => {
  if (!text) return false;

  if (window.navigator.clipboard?.writeText) {
    try {
      await window.navigator.clipboard.writeText(text);
      return true;
    } catch (_error) {
      // Fall through to the legacy copy path below.
    }
  }

  const helper = document.createElement('textarea');
  helper.value = text;
  helper.setAttribute('readonly', '');
  helper.style.position = 'fixed';
  helper.style.top = '-9999px';
  helper.style.left = '-9999px';
  document.body.appendChild(helper);
  helper.focus();
  helper.select();

  let copied = false;
  try {
    copied = document.execCommand('copy');
  } catch (_error) {
    copied = false;
  }

  document.body.removeChild(helper);
  return copied;
};

const syncNativeShareButtons = (root = document) => {
  root.querySelectorAll('[data-native-share]').forEach((button) => {
    button.classList.toggle('d-none', !window.navigator.share);
  });
};

const syncReviewModalShareControls = (modal) => {
  if (!modal) return;

  const shareControls = modal.querySelector('[data-modal-share-controls]');
  if (!shareControls) return;

  const metadata = getModalShareMetadata(modal);
  const copyButton = shareControls.querySelector('[data-copy-share]');
  const emailLink = shareControls.querySelector('[data-share-email]');
  const nativeShareButton = shareControls.querySelector('[data-native-share]');

  if (!metadata) {
    if (copyButton) {
      copyButton.dataset.shareUrl = '';
      copyButton.disabled = true;
    }

    if (emailLink) {
      emailLink.setAttribute('href', '#');
      emailLink.classList.add('disabled');
      emailLink.setAttribute('aria-disabled', 'true');
    }

    if (nativeShareButton) {
      nativeShareButton.dataset.shareTitle = '';
      nativeShareButton.dataset.shareText = '';
      nativeShareButton.dataset.shareUrl = '';
      nativeShareButton.disabled = true;
    }

    return;
  }

  const shareUrl = metadata.dataset.shareUrl || '';
  const shareTitle = metadata.dataset.shareTitle || '';
  const shareText = metadata.dataset.shareText || '';
  const emailUrl = metadata.dataset.shareEmailUrl || '';

  if (copyButton) {
    copyButton.dataset.shareUrl = shareUrl;
    copyButton.disabled = !shareUrl;
  }

  if (emailLink) {
    emailLink.setAttribute('href', emailUrl || '#');
    emailLink.classList.toggle('disabled', !emailUrl);
    if (emailUrl) {
      emailLink.removeAttribute('aria-disabled');
    } else {
      emailLink.setAttribute('aria-disabled', 'true');
    }
  }

  if (nativeShareButton) {
    nativeShareButton.dataset.shareTitle = shareTitle;
    nativeShareButton.dataset.shareText = shareText;
    nativeShareButton.dataset.shareUrl = shareUrl;
    nativeShareButton.disabled = !shareUrl;
  }

  syncNativeShareButtons(shareControls);
};

const setSharedReviewModalLoading = (modal, isLoading) => {
  if (!modal) return;

  const controls = modal.querySelectorAll(
    '.btn-close, [data-bs-dismiss="modal"]',
  );
  controls.forEach((control) => {
    control.disabled = isLoading;
  });
};

const loadSharedReviewModalContent = async (modal, trigger) => {
  const container = getSharedReviewModalContainer(modal);
  if (!trigger || !container) return;

  setSharedReviewModalLoading(modal, true);

  try {
    const response = await window.fetch(trigger.href, {
      headers: { 'HX-Request': 'true' },
      credentials: 'same-origin',
    });
    if (!response.ok) return;

    const html = await response.text();
    container.innerHTML = html;
  } finally {
    setSharedReviewModalLoading(modal, false);
  }

  syncReviewModalShareControls(modal);
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

const snapshotCurrentDockMarkup = () => {
  const mobileSlot = mobileDockSlot();
  const desktopSlot = desktopDockSlot();

  mobileDockDefaultMarkup = mobileSlot ? mobileSlot.innerHTML : null;
  desktopDockDefaultMarkup = desktopSlot ? desktopSlot.innerHTML : null;
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

  updateMobileToolbarState();
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

  updateMobileToolbarState();
};

const updateMobileToolbarState = () => {
  const mobileSlot = mobileDockSlot();
  const mobileInner = mobileToolbarInner();
  if (!mobileSlot || !mobileInner) return;

  const hasVisibleActions = mobileSlot.children.length > 0;
  mobileInner.classList.toggle(
    'sticky-mobile-toolbar__inner--theme-only',
    !hasVisibleActions,
  );
};

const getReviewModalIds = () =>
  Array.from(document.querySelectorAll('.modal[id^="modal-"]')).map(
    (modal) => modal.id,
  );

const updateReviewModalNavigator = (modal) => {
  const sharedTriggers = getSharedReviewModalTriggers(modal.id);
  if (sharedTriggers.length) {
    const index = sharedReviewModalIndices.get(modal.id) ?? -1;
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
      nextButton.disabled = index >= sharedTriggers.length - 1;
      nextButton.dataset.targetIndex = String(index + 1);
      nextButton.dataset.currentModalId = modal.id;
    });

    positions.forEach((position) => {
      position.textContent = `Review ${index + 1} of ${sharedTriggers.length}`;
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

  syncReviewModalShareControls(modal);

  if (getSharedReviewModalTriggers(modal.id).length) {
    const trigger = event.relatedTarget;
    const triggers = getSharedReviewModalTriggers(modal.id);
    sharedReviewModalIndices.set(modal.id, trigger ? triggers.indexOf(trigger) : -1);
    await loadSharedReviewModalContent(modal, trigger);
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
    sharedReviewModalIndices.delete(modal.id);
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

  if (getSharedReviewModalTriggers(modal.id).length) {
    sharedReviewModalIndices.delete(modal.id);
    const container = getSharedReviewModalContainer(modal);
    if (container) {
      container.innerHTML = '';
    }
    syncReviewModalShareControls(modal);
  }

  restoreDefaultDockMarkup();
  cleanupModalArtifacts();
});

rememberDefaultDockMarkup();
updateMobileToolbarState();
syncNativeShareButtons();

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
  const copyButton = event.target.closest('[data-copy-share]');
  if (copyButton) {
    const shareUrl = toAbsoluteUrl(copyButton.dataset.shareUrl);
    if (!shareUrl) return;

    const copied = await copyTextToClipboard(shareUrl);
    if (copied) {
      const label = copyButton.querySelector('span');
      const originalText = label ? label.textContent : '';
      if (label) {
        label.textContent = 'Copied';
      }
      window.setTimeout(() => {
        if (label) {
          label.textContent = originalText;
        }
      }, 1600);
    }
    return;
  }

  const nativeShareButton = event.target.closest('[data-native-share]');
  if (nativeShareButton && window.navigator.share) {
    const shareUrl = toAbsoluteUrl(nativeShareButton.dataset.shareUrl);
    if (!shareUrl) return;

    try {
      await window.navigator.share({
        title: nativeShareButton.dataset.shareTitle || document.title,
        text: nativeShareButton.dataset.shareText || '',
        url: shareUrl,
      });
    } catch (_error) {
      // Treat cancelled share dialogs as a no-op.
    }
    return;
  }

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

  if (getSharedReviewModalTriggers(currentModalId).length) {
    const targetIndex = Number(button.dataset.targetIndex);
    const triggers = getSharedReviewModalTriggers(currentModalId);
    const targetTrigger = triggers[targetIndex];
    const modal = document.getElementById(currentModalId);
    if (!targetTrigger || !modal) return;

    sharedReviewModalIndices.set(currentModalId, targetIndex);
    await loadSharedReviewModalContent(modal, targetTrigger);
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

document.body.addEventListener('htmx:afterSettle', () => {
  if (!document.querySelector('.modal.show')) {
    snapshotCurrentDockMarkup();
  }

  updateMobileToolbarState();
  updateIssueReviewNavigator();
  syncNativeShareButtons();
  if (typeof scrollSpy.refresh === 'function') {
    scrollSpy.refresh();
  }
});

document.body.addEventListener('htmx:afterSwap', (event) => {
  const modal = event.target.closest('.modal');
  if (!modal || !event.target.matches('[data-review-modal-container]')) return;

  syncReviewModalShareControls(modal);
});
