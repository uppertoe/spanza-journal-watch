import '../sass/project.scss';

/* Project specific Javascript goes here. */
const { Popover, ScrollSpy, Modal } = window.bootstrap;

const popoverTriggerList = document.querySelectorAll(
  '[data-bs-toggle="popover"]',
);

const popoverList = [...popoverTriggerList].map(
  (popoverTriggerEl) => new Popover(popoverTriggerEl),
);

const contentsListGroup = document.getElementById('contents-list-group');
var scrollSpy = contentsListGroup
  ? new ScrollSpy(document.body, {
      target: '#contents-list-group',
    })
  : null;

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
const analyticsEndpoint = '/reader/action';
const reviewSessionThresholdMs = 5000;
const reviewSessions = new Map();
let reviewSessionCounter = 0;
let issueNavigatorFrame = null;
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

const getCookie = (name) => {
  const cookieValue = `; ${document.cookie}`;
  const cookieParts = cookieValue.split(`; ${name}=`);
  if (cookieParts.length !== 2) return '';
  return decodeURIComponent(cookieParts.pop().split(';').shift());
};

const sendAnalyticsEvent = (payload, { beacon = false } = {}) => {
  const body = JSON.stringify(payload);

  if (beacon && navigator.sendBeacon) {
    const blob = new Blob([body], { type: 'application/json' });
    navigator.sendBeacon(analyticsEndpoint, blob);
    return Promise.resolve(true);
  }

  return window
    .fetch(analyticsEndpoint, {
      method: 'POST',
      credentials: 'same-origin',
      headers: {
        'Content-Type': 'application/json',
        'X-CSRFToken': getCookie('csrftoken'),
      },
      body,
      keepalive: beacon,
    })
    .then((response) => response.ok)
    .catch(() => false);
};

const getReviewAnalyticsContext = (element) => {
  const reviewRoot = element?.closest('[data-analytics-review-id]');
  if (!reviewRoot) return null;

  const reviewId = Number(reviewRoot.dataset.analyticsReviewId);
  if (!reviewId) return null;

  return {
    element: reviewRoot,
    reviewId,
    source: reviewRoot.dataset.analyticsSource || '',
  };
};

const trackReviewAnalytics = (element, eventType, extra = {}, options = {}) => {
  const context = getReviewAnalyticsContext(element);
  if (!context) return Promise.resolve(false);

  return sendAnalyticsEvent(
    {
      event_type: eventType,
      review_id: context.reviewId,
      source: context.source,
      ...extra,
    },
    options,
  );
};

const getSearchFilterState = () => {
  const form = document.getElementById('search-filter-form');
  if (!form) return {};

  const formData = new window.FormData(form);
  return {
    query: (formData.get('q') || '').toString(),
    selected_year: (formData.get('year') || '').toString(),
    selected_tags: formData.getAll('tag'),
  };
};

const getReviewSessionKey = (element) => {
  if (!element.dataset.analyticsSessionKey) {
    reviewSessionCounter += 1;
    element.dataset.analyticsSessionKey = `review-session-${reviewSessionCounter}`;
  }
  return element.dataset.analyticsSessionKey;
};

const ensureReviewSession = (element) => {
  const context = getReviewAnalyticsContext(element);
  if (!context) return null;

  const key = getReviewSessionKey(context.element);
  if (!reviewSessions.has(key)) {
    reviewSessions.set(key, {
      key,
      element: context.element,
      reviewId: context.reviewId,
      source: context.source,
      openSent: false,
      visibleSince: null,
      totalVisibleMs: 0,
      engagedSent: false,
    });
  }

  return reviewSessions.get(key);
};

const openReviewSession = (element, { immediate = false } = {}) => {
  const session = ensureReviewSession(element);
  if (!session) return;

  if (!session.openSent) {
    sendAnalyticsEvent({
      event_type: 'review_open',
      review_id: session.reviewId,
      source: session.source,
    });
    session.openSent = true;
  }

  if (session.visibleSince === null) {
    session.visibleSince = window.performance.now();
  }
};

const pauseReviewSession = (element) => {
  const session = ensureReviewSession(element);
  if (!session || session.visibleSince === null) return;

  session.totalVisibleMs += Math.max(
    0,
    Math.round(window.performance.now() - session.visibleSince),
  );
  session.visibleSince = null;
};

const flushReviewSession = (element, { beacon = true } = {}) => {
  const session = ensureReviewSession(element);
  if (!session) return;

  pauseReviewSession(session.element);

  if (session.engagedSent || session.totalVisibleMs < reviewSessionThresholdMs) {
    return;
  }

  sendAnalyticsEvent(
    {
      event_type: 'review_engaged',
      review_id: session.reviewId,
      source: session.source,
      duration_ms: session.totalVisibleMs,
    },
    { beacon },
  );
  session.engagedSent = true;
};

const reviewVisibilityObserver = new window.IntersectionObserver(
  (entries) => {
    entries.forEach((entry) => {
      const reviewElement = entry.target;
      if (entry.isIntersecting && entry.intersectionRatio >= 0.25) {
        openReviewSession(reviewElement);
      } else {
        pauseReviewSession(reviewElement);
      }
    });
  },
  {
    threshold: [0, 0.25],
  },
);

const observeAnalyticsReviewElements = (root = document) => {
  root.querySelectorAll('[data-analytics-review-id]').forEach((element) => {
    ensureReviewSession(element);
    if (element.dataset.analyticsObserved === 'true') return;
    element.dataset.analyticsObserved = 'true';
    reviewVisibilityObserver.observe(element);
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

  const currentReviewElement = container.querySelector('[data-analytics-review-id]');
  if (currentReviewElement) {
    flushReviewSession(currentReviewElement);
  }

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
  observeAnalyticsReviewElements(container);
  const nextReviewElement = container.querySelector('[data-analytics-review-id]');
  if (nextReviewElement) {
    openReviewSession(nextReviewElement, { immediate: true });
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
  const currentReviewElement = modal.querySelector('[data-analytics-review-id]');
  if (currentReviewElement) {
    flushReviewSession(currentReviewElement);
  }
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
observeAnalyticsReviewElements();

document.addEventListener('visibilitychange', () => {
  if (document.visibilityState === 'hidden') {
    reviewSessions.forEach((session) => flushReviewSession(session.element));
  }
});

window.addEventListener('pagehide', () => {
  reviewSessions.forEach((session) => flushReviewSession(session.element));
});

const getIssueReviewArticles = () =>
  Array.from(document.querySelectorAll('#articles .article-post[id^="list-item-"]'));

const hasIssueReviewNavigator = () =>
  !!document.querySelector('[data-issue-review-nav="prev"], [data-issue-review-nav="next"]');

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
  issueNavigatorFrame = null;

  if (!hasIssueReviewNavigator()) return;

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

const scheduleIssueReviewNavigatorUpdate = () => {
  if (issueNavigatorFrame !== null) return;
  issueNavigatorFrame = window.requestAnimationFrame(updateIssueReviewNavigator);
};

document.addEventListener('click', async (event) => {
  const emailShareLink = event.target.closest('[data-share-email]');
  if (emailShareLink && !emailShareLink.classList.contains('disabled')) {
    trackReviewAnalytics(emailShareLink, 'review_share_email', {}, { beacon: true });
    return;
  }

  const socialShareLink = event.target.closest(
    '.share-chip--bluesky, .share-chip--x, .share-chip--facebook',
  );
  if (socialShareLink) {
    let eventType = '';
    if (socialShareLink.classList.contains('share-chip--bluesky')) {
      eventType = 'review_share_bluesky';
    } else if (socialShareLink.classList.contains('share-chip--x')) {
      eventType = 'review_share_x';
    } else if (socialShareLink.classList.contains('share-chip--facebook')) {
      eventType = 'review_share_facebook';
    }
    if (eventType) {
      trackReviewAnalytics(socialShareLink, eventType, {}, { beacon: true });
    }
    return;
  }

  const fullTextLink = event.target.closest('[data-analytics-full-text]');
  if (fullTextLink) {
    trackReviewAnalytics(fullTextLink, 'review_full_text_click', {}, { beacon: true });
    return;
  }

  const searchResultLink = event.target.closest('[data-search-result-click]');
  if (searchResultLink) {
    const reviewId = Number(searchResultLink.dataset.reviewId || 0);
    sendAnalyticsEvent(
      {
        event_type: 'search_result_click',
        ...(reviewId ? { review_id: reviewId } : {}),
        source: 'search_results',
        metadata: getSearchFilterState(),
      },
      { beacon: true },
    );
    return;
  }

  const copyButton = event.target.closest('[data-copy-share]');
  if (copyButton) {
    const shareUrl = toAbsoluteUrl(copyButton.dataset.shareUrl);
    if (!shareUrl) return;

    const copied = await copyTextToClipboard(shareUrl);
    if (copied) {
      trackReviewAnalytics(copyButton, 'review_share_copy_link');
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
      trackReviewAnalytics(nativeShareButton, 'review_share_native');
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
      window.setTimeout(scheduleIssueReviewNavigatorUpdate, 150);
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

window.addEventListener('scroll', scheduleIssueReviewNavigatorUpdate, {
  passive: true,
});
window.addEventListener('load', scheduleIssueReviewNavigatorUpdate);

document.body.addEventListener('htmx:afterSettle', () => {
  if (!document.querySelector('.modal.show')) {
    snapshotCurrentDockMarkup();
  }

  updateMobileToolbarState();
  scheduleIssueReviewNavigatorUpdate();
  syncNativeShareButtons();
  observeAnalyticsReviewElements();
  if (typeof scrollSpy.refresh === 'function') {
    scrollSpy.refresh();
  }
});

document.body.addEventListener('htmx:afterSwap', (event) => {
  const modal = event.target.closest('.modal');
  if (!modal || !event.target.matches('[data-review-modal-container]')) return;

  syncReviewModalShareControls(modal);
  observeAnalyticsReviewElements(event.target);
});
