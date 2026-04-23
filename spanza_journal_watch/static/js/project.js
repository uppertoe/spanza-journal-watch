/* Project specific Javascript goes here. */
const { Popover, Modal, Toast } = window.bootstrap;
const popoverInstances = new WeakMap();

/* ── Auto-show Django message toasts ─────────────────────── */
function showAllToasts(root) {
  const toasts = (root || document).querySelectorAll(
    '.jw-toast-stack .toast:not(.show)',
  );
  toasts.forEach(function (el) {
    Toast.getOrCreateInstance(el).show();
  });
}
showAllToasts();
// After HTMX OOB swap delivers new messages
document.body.addEventListener('htmx:oobAfterSwap', function (event) {
  if (event.detail.target && event.detail.target.id === 'oob-message') {
    showAllToasts(event.detail.target);
  }
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
let activeDockModalId = null;
const sharedReviewModalIndices = new Map();

const mobileDockSlot = () => document.getElementById('mobile-action-dock-slot');
const desktopDockSlot = () =>
  document.getElementById('desktop-action-dock-slot');
const mobileToolbarInner = () =>
  document.querySelector('.sticky-mobile-toolbar__inner');
const analyticsEndpoint = '/reader/action';
const reviewSessionThresholdMs = 5000;
const reviewSessions = new Map();
let reviewSessionCounter = 0;

const generateShareToken = () => {
  const chars =
    'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789';
  let token = '';
  for (let i = 0; i < 8; i++) {
    token += chars.charAt(Math.floor(Math.random() * chars.length));
  }
  return token;
};

const appendShareToken = (url, token) => {
  if (!url || !token) return url;
  try {
    const parsed = new URL(url, window.location.origin);
    parsed.searchParams.set('ref', token);
    return parsed.toString();
  } catch (_e) {
    return url;
  }
};

let pageScrollDepthMax = 0;
let pageScrollDepthFlushed = false;

const updateScrollDepth = () => {
  const docHeight = document.documentElement.scrollHeight - window.innerHeight;
  if (docHeight <= 0) return;
  const pct = Math.min(100, Math.round((window.scrollY / docHeight) * 100));
  if (pct > pageScrollDepthMax) pageScrollDepthMax = pct;
};

const flushScrollDepth = () => {
  if (pageScrollDepthFlushed || pageScrollDepthMax <= 0) return;
  pageScrollDepthFlushed = true;
  const pageMeta = document.querySelector('[data-analytics-page]');
  const page = pageMeta ? pageMeta.dataset.analyticsPage : '';
  if (!page) return;
  sendAnalyticsEvent(
    {
      event_type: 'page_visit',
      source: 'scroll_depth',
      scroll_depth: pageScrollDepthMax,
      metadata: { page },
    },
    { beacon: true },
  );
};

window.addEventListener('scroll', updateScrollDepth, { passive: true });
document.addEventListener('visibilitychange', () => {
  if (document.visibilityState === 'hidden') flushScrollDepth();
});
window.addEventListener('pagehide', flushScrollDepth);
let cachedIssueReviewArticles = [];
let cachedIssuePrevButtons = [];
let cachedIssueNextButtons = [];
let cachedIssueContentsLinks = [];
let cachedIssuePositionBadges = [];
let activeIssueReviewIndex = -1;
let issueReviewObserver = null;
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
    const available = typeof window.navigator.share === 'function';
    button.hidden = !available;
    button.setAttribute('aria-hidden', available ? 'false' : 'true');
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

  if (
    session.engagedSent ||
    session.totalVisibleMs < reviewSessionThresholdMs
  ) {
    return;
  }

  sendAnalyticsEvent(
    {
      event_type: 'review_engaged',
      review_id: session.reviewId,
      source: session.source,
      duration_ms: session.totalVisibleMs,
      scroll_depth: pageScrollDepthMax || null,
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

const getOrCreatePopover = (element) => {
  if (!element) return null;

  if (!popoverInstances.has(element)) {
    popoverInstances.set(
      element,
      new Popover(element, {
        trigger: element.dataset.bsTrigger || 'focus',
        customClass: element.dataset.bsCustomClass || '',
      }),
    );
  }

  return popoverInstances.get(element);
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

  const currentReviewElement = container.querySelector(
    '[data-analytics-review-id]',
  );
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
  window.requestAnimationFrame(() => {
    if (window.htmx) {
      window.htmx.process(container);
    }
    syncReviewModalShareControls(modal);
    syncNativeShareButtons(container);
    observeAnalyticsReviewElements(container);
    const modalBody = container.closest('.modal-body');
    if (modalBody) modalBody.scrollTop = 0;
    const nextReviewElement = container.querySelector(
      '[data-analytics-review-id]',
    );
    if (nextReviewElement) {
      openReviewSession(nextReviewElement, { immediate: true });
    }
  });
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

// ── Dock slot swap: show modal nav without destroying existing DOM nodes ──
// Stash original children in a DocumentFragment so they keep HTMX bindings.
const _savedDockChildren = { mobile: null, desktop: null };

const swapDockToModalNav = (modal) => {
  if (!modal?.id) return;
  const template = document.querySelector(
    `[data-review-modal-dock-template="${modal.id}"]`,
  );
  const markup = template?.innerHTML?.trim();
  if (!markup) return;

  [
    ['mobile', mobileDockSlot()],
    ['desktop', desktopDockSlot()],
  ].forEach(([key, slot]) => {
    if (!slot) return;
    // Save existing children into a fragment (preserves DOM nodes + HTMX state)
    if (!_savedDockChildren[key]) {
      const frag = document.createDocumentFragment();
      while (slot.firstChild) frag.appendChild(slot.firstChild);
      _savedDockChildren[key] = frag;
    }
    slot.innerHTML = markup;
  });

  activeDockModalId = modal.id;
  updateMobileToolbarState();
};

const restoreDockFromModalNav = () => {
  [
    ['mobile', mobileDockSlot()],
    ['desktop', desktopDockSlot()],
  ].forEach(([key, slot]) => {
    if (!slot) return;
    // Remove modal nav content
    slot.innerHTML = '';
    // Re-attach the original children (HTMX bindings intact)
    if (_savedDockChildren[key]) {
      slot.appendChild(_savedDockChildren[key]);
      _savedDockChildren[key] = null;
    }
  });

  updateMobileToolbarState();
  activeDockModalId = null;
};

const getReviewModalIds = () =>
  Array.from(document.querySelectorAll('.modal[id^="modal-"]')).map(
    (modal) => modal.id,
  );

const updateReviewModalNavigator = (modal) => {
  const compactPositionLabel = (current, total) =>
    window.matchMedia('(max-width: 575.98px)').matches
      ? `${current}/${total}`
      : `Review ${current} of ${total}`;

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
      position.textContent = compactPositionLabel(
        index + 1,
        sharedTriggers.length,
      );
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
    position.textContent = compactPositionLabel(index + 1, modalIds.length);
  });
};

const cleanupModalArtifacts = () => {
  const anyOpenModal = document.querySelector('.modal.show');
  if (anyOpenModal) return;

  if (activeDockModalId) {
    restoreDockFromModalNav();
  }

  document.querySelectorAll('.modal-backdrop').forEach((el) => el.remove());
  document.body.classList.remove('modal-open');
  document.body.classList.remove('jw-modal-open');
  document.documentElement.classList.remove('jw-modal-open');
  document.body.style.removeProperty('overflow');
  document.body.style.removeProperty('padding-right');
};

document.addEventListener('show.bs.modal', (event) => {
  const modal = event.target;
  if (!modal.id || closingModalFromPopStateId) return;
  document.body.classList.add('jw-modal-open');
  document.documentElement.classList.add('jw-modal-open');

  if (getSharedReviewModalTriggers(modal.id).length) {
    const trigger = event.relatedTarget;
    const triggers = getSharedReviewModalTriggers(modal.id);
    sharedReviewModalIndices.set(
      modal.id,
      trigger ? triggers.indexOf(trigger) : -1,
    );
    window.requestAnimationFrame(() => {
      loadSharedReviewModalContent(modal, trigger);
    });
  } else {
    syncReviewModalShareControls(modal);
  }

  swapDockToModalNav(modal);
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

window.addEventListener('resize', () => {
  const openModalEl = document.querySelector('.modal.show');
  if (openModalEl) {
    updateReviewModalNavigator(openModalEl);
  }
});

document.addEventListener('hidden.bs.modal', (event) => {
  const modal = event.target;
  const currentReviewElement = modal.querySelector(
    '[data-analytics-review-id]',
  );
  if (currentReviewElement) {
    flushReviewSession(currentReviewElement);
  }
  if (!modal.id) {
    cleanupModalArtifacts();
    return;
  }

  if (closingModalFromPopStateId && closingModalFromPopStateId === modal.id) {
    closingModalFromPopStateId = null;
    sharedReviewModalIndices.delete(modal.id);
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

  cleanupModalArtifacts();
});

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

const refreshIssueReviewNavigatorCache = () => {
  cachedIssueReviewArticles = Array.from(
    document.querySelectorAll('#articles .article-post[id^="list-item-"]'),
  );
  cachedIssuePrevButtons = Array.from(
    document.querySelectorAll('[data-issue-review-nav="prev"]'),
  );
  cachedIssueNextButtons = Array.from(
    document.querySelectorAll('[data-issue-review-nav="next"]'),
  );
  cachedIssueContentsLinks = Array.from(
    document.querySelectorAll('#list-reviews a[href^="#list-item-"]'),
  );
  cachedIssuePositionBadges = Array.from(
    document.querySelectorAll('[data-issue-review-position]'),
  );
};

const getIssueReviewArticles = () => cachedIssueReviewArticles;

const hasIssueReviewNavigator = () =>
  cachedIssuePrevButtons.length > 0 || cachedIssueNextButtons.length > 0;

const updateContentsListActiveState = (index) => {
  cachedIssueContentsLinks.forEach((link, linkIndex) => {
    const isActive = linkIndex === index;
    link.classList.toggle('active', isActive);
    link.setAttribute('aria-current', isActive ? 'true' : 'false');
  });
};

const updateIssueReviewNavigator = (index = activeIssueReviewIndex) => {
  if (!hasIssueReviewNavigator()) return;

  const reviews = getIssueReviewArticles();
  if (!reviews.length) return;
  if (index < 0 || index >= reviews.length) return;

  activeIssueReviewIndex = index;

  cachedIssuePrevButtons.forEach((button) => {
    const shouldDisable = index <= 0;
    const targetIndex = String(index - 1);
    if (button.disabled !== shouldDisable) {
      button.disabled = shouldDisable;
    }
    if (button.dataset.targetIndex !== targetIndex) {
      button.dataset.targetIndex = targetIndex;
    }
  });

  cachedIssueNextButtons.forEach((button) => {
    const shouldDisable = index >= reviews.length - 1;
    const targetIndex = String(index + 1);
    if (button.disabled !== shouldDisable) {
      button.disabled = shouldDisable;
    }
    if (button.dataset.targetIndex !== targetIndex) {
      button.dataset.targetIndex = targetIndex;
    }
  });

  var total = reviews.length;
  var label = String(index + 1) + ' / ' + String(total);
  cachedIssuePositionBadges.forEach(function (badge) {
    badge.textContent = label;
  });

  updateContentsListActiveState(index);
};

const setupIssueReviewObserver = () => {
  if (issueReviewObserver) {
    issueReviewObserver.disconnect();
    issueReviewObserver = null;
  }

  const reviews = getIssueReviewArticles();
  if (!reviews.length) return;

  issueReviewObserver = new window.IntersectionObserver(
    (entries) => {
      const visibleEntries = entries
        .filter((entry) => entry.isIntersecting)
        .sort(
          (left, right) =>
            Math.abs(left.boundingClientRect.top) -
            Math.abs(right.boundingClientRect.top),
        );

      const nextEntry = visibleEntries[0];
      if (!nextEntry) return;

      const nextIndex = reviews.indexOf(nextEntry.target);
      if (nextIndex === -1 || nextIndex === activeIssueReviewIndex) return;

      updateIssueReviewNavigator(nextIndex);
    },
    {
      root: null,
      rootMargin: '-18% 0px -62% 0px',
      threshold: 0,
    },
  );

  reviews.forEach((review) => issueReviewObserver.observe(review));

  const firstVisibleIndex = reviews.findIndex((review) => {
    const rect = review.getBoundingClientRect();
    return rect.top >= 0 && rect.top < window.innerHeight * 0.45;
  });

  updateIssueReviewNavigator(firstVisibleIndex === -1 ? 0 : firstVisibleIndex);
};

document.addEventListener('click', async (event) => {
  const emailShareLink = event.target.closest('[data-share-email]');
  if (emailShareLink && !emailShareLink.classList.contains('disabled')) {
    const token = generateShareToken();
    trackReviewAnalytics(
      emailShareLink,
      'review_share_email',
      { metadata: { share_token: token } },
      { beacon: true },
    );
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
      const token = generateShareToken();
      trackReviewAnalytics(
        socialShareLink,
        eventType,
        { metadata: { share_token: token } },
        { beacon: true },
      );
    }
    return;
  }

  const fullTextLink = event.target.closest('[data-analytics-full-text]');
  if (fullTextLink) {
    trackReviewAnalytics(
      fullTextLink,
      'review_full_text_click',
      {},
      { beacon: true },
    );
    return;
  }

  const relatedLink = event.target.closest('[data-analytics-related-link]');
  if (relatedLink) {
    const reviewId = Number(relatedLink.dataset.reviewId || 0);
    const sourceContext = relatedLink.dataset.sourceContext || '';
    const sourceReviewRoot = relatedLink.closest('[data-analytics-review-id]');
    const sourceReviewId = sourceReviewRoot
      ? Number(sourceReviewRoot.dataset.analyticsReviewId) || null
      : null;
    if (reviewId) {
      sendAnalyticsEvent(
        {
          event_type: 'review_related_click',
          review_id: reviewId,
          source: sourceContext,
          metadata: sourceReviewId ? { source_review_id: sourceReviewId } : {},
        },
        { beacon: true },
      );
    }
    // Don't `return` — existing htmx/modal handlers for this link must still run.
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
    const token = generateShareToken();
    const shareUrl = appendShareToken(
      toAbsoluteUrl(copyButton.dataset.shareUrl),
      token,
    );
    if (!shareUrl) return;

    const copied = await copyTextToClipboard(shareUrl);
    if (copied) {
      trackReviewAnalytics(copyButton, 'review_share_copy_link', {
        metadata: { share_token: token },
      });
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
    const token = generateShareToken();
    const shareUrl = appendShareToken(
      toAbsoluteUrl(nativeShareButton.dataset.shareUrl),
      token,
    );
    if (!shareUrl) return;

    try {
      await window.navigator.share({
        title: nativeShareButton.dataset.shareTitle || document.title,
        text: nativeShareButton.dataset.shareText || '',
        url: shareUrl,
      });
      trackReviewAnalytics(nativeShareButton, 'review_share_native', {
        metadata: { share_token: token },
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
    swapDockToModalNav(modal);
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

document.addEventListener('click', (event) => {
  const button = event.target.closest('[data-review-modal-close]');
  if (!button) return;

  const currentModalId = button.dataset.currentModalId;
  if (!currentModalId) return;

  const currentModal = document.getElementById(currentModalId);
  if (!currentModal) return;

  Modal.getOrCreateInstance(currentModal).hide();
});

document.addEventListener('pointerdown', (event) => {
  const popoverTrigger = event.target.closest('[data-bs-toggle="popover"]');
  if (popoverTrigger) {
    getOrCreatePopover(popoverTrigger);
  }

  const button = event.target.closest('[data-review-modal-nav]');
  if (!button || button.disabled) return;

  event.preventDefault();
});

document.addEventListener('focusin', (event) => {
  const popoverTrigger = event.target.closest('[data-bs-toggle="popover"]');
  if (popoverTrigger) {
    getOrCreatePopover(popoverTrigger);
  }
});

document.addEventListener('mouseover', (event) => {
  const popoverTrigger = event.target.closest('[data-bs-toggle="popover"]');
  if (popoverTrigger) {
    getOrCreatePopover(popoverTrigger);
  }
});

refreshIssueReviewNavigatorCache();
setupIssueReviewObserver();

document.body.addEventListener('htmx:afterSettle', () => {
  updateMobileToolbarState();
  refreshIssueReviewNavigatorCache();
  setupIssueReviewObserver();
  syncNativeShareButtons();
  observeAnalyticsReviewElements();
});

document.body.addEventListener('htmx:afterSwap', (event) => {
  const modal = event.target.closest('.modal');
  if (!modal || !event.target.matches('[data-review-modal-container]')) return;

  // Scroll modal body to top when new review content loads
  const modalBody = modal.querySelector('.modal-body');
  if (modalBody) modalBody.scrollTop = 0;

  syncReviewModalShareControls(modal);
  observeAnalyticsReviewElements(event.target);
});

/* ── Journal browser: login prompt (desktop toast + mobile bar) ── */
document.body.addEventListener('showLoginPrompt', (event) => {
  const count = event.detail?.count || 0;

  /* Desktop toast */
  const toastEl = document.getElementById('login-prompt-toast');
  if (toastEl) {
    const countEl = toastEl.querySelector('[data-star-count]');
    if (countEl) countEl.textContent = count;
    const toast = window.bootstrap.Toast.getOrCreateInstance(toastEl, {
      delay: 8000,
    });
    toast.show();
  }

  /* Mobile bar */
  const mobileEl = document.getElementById('mobile-login-prompt');
  if (mobileEl) {
    const countEl = mobileEl.querySelector('[data-star-count-mobile]');
    if (countEl) countEl.textContent = count;
    mobileEl.classList.remove('d-none');
  }
});

/* ── Journal browser: smooth scroll for TOC links ────────── */
document.addEventListener('click', (event) => {
  const link = event.target.closest('.journal-toc__link');
  if (!link) return;
  const targetId = link.getAttribute('href');
  if (!targetId || !targetId.startsWith('#')) return;
  const target = document.querySelector(targetId);
  if (!target) return;
  event.preventDefault();
  target.scrollIntoView({ behavior: 'smooth', block: 'start' });
});

/* ── Journal shelf: visibility toggling + horizontal scroll ────── */
(function () {
  var grid = document.getElementById('journal-shelf-grid');
  if (!grid) return;

  var hiddenBar = document.getElementById('shelf-hidden-bar');
  var hiddenCount = document.getElementById('shelf-hidden-count');
  var showAllBtn = document.getElementById('shelf-show-all-btn');
  var arrowLeft = document.getElementById('shelf-arrow-left');
  var arrowRight = document.getElementById('shelf-arrow-right');
  var dotsContainer = document.getElementById('shelf-dots');

  function updateHiddenBar() {
    var total = grid.querySelectorAll('.journal-book--hidden').length;
    if (hiddenBar) {
      if (total > 0) {
        hiddenBar.classList.remove('d-none');
        hiddenCount.textContent = total;
      } else {
        hiddenBar.classList.add('d-none');
      }
    }
    updateDots();
    updateArrows();
  }

  // Hide a journal — instant DOM toggle + persist to session
  grid.addEventListener('click', function (e) {
    var btn = e.target.closest('[data-hide-journal]');
    if (!btn) return;
    e.preventDefault();
    e.stopPropagation();
    var id = btn.getAttribute('data-hide-journal');
    var book = btn.closest('.journal-book');
    if (book) book.classList.add('journal-book--hidden');
    updateHiddenBar();
    navigator.sendBeacon('/journals/shelf/hide/' + id + '/');
  });

  // Show all — instant DOM toggle + persist to session
  if (showAllBtn) {
    showAllBtn.addEventListener('click', function () {
      grid.querySelectorAll('.journal-book--hidden').forEach(function (b) {
        b.classList.remove('journal-book--hidden');
      });
      updateHiddenBar();
      navigator.sendBeacon('/journals/shelf/show-all/');
    });
  }

  // --- Horizontal scroll arrows (desktop) ---
  function updateArrows() {
    if (!arrowLeft || !arrowRight) return;
    // Only relevant on lg+ when grid has overflow (shouldn't normally, but handle gracefully)
    var isScrollable = grid.scrollWidth > grid.clientWidth + 2;
    if (!isScrollable) {
      arrowLeft.classList.add('shelf-arrow--hidden');
      arrowRight.classList.add('shelf-arrow--hidden');
      return;
    }
    arrowLeft.classList.toggle('shelf-arrow--hidden', grid.scrollLeft < 4);
    arrowRight.classList.toggle(
      'shelf-arrow--hidden',
      grid.scrollLeft + grid.clientWidth >= grid.scrollWidth - 4,
    );
  }

  if (arrowLeft) {
    arrowLeft.addEventListener('click', function () {
      grid.scrollBy({ left: -grid.clientWidth * 0.8, behavior: 'smooth' });
    });
  }
  if (arrowRight) {
    arrowRight.addEventListener('click', function () {
      grid.scrollBy({ left: grid.clientWidth * 0.8, behavior: 'smooth' });
    });
  }

  grid.addEventListener(
    'scroll',
    function () {
      updateArrows();
      updateActiveDot();
    },
    { passive: true },
  );

  // --- Mobile page dots ---
  function getVisibleBooks() {
    return Array.prototype.filter.call(
      grid.querySelectorAll('.journal-book[data-journal-id]'),
      function (b) {
        return !b.classList.contains('journal-book--hidden');
      },
    );
  }

  function updateDots() {
    if (!dotsContainer) return;
    // Only show dots on mobile when there's overflow
    var isScrollable = grid.scrollWidth > grid.clientWidth + 2;
    if (!isScrollable) {
      dotsContainer.innerHTML = '';
      return;
    }
    var visible = getVisibleBooks();
    var booksPerPage = Math.max(1, Math.floor(grid.clientWidth / 176)); // ~11rem
    var pages = Math.ceil(visible.length / booksPerPage);
    if (pages <= 1) {
      dotsContainer.innerHTML = '';
      return;
    }
    var html = '';
    for (var i = 0; i < pages; i++) {
      html +=
        '<span class="journal-shelf-dot' +
        (i === 0 ? ' active' : '') +
        '" data-dot-page="' +
        i +
        '"></span>';
    }
    dotsContainer.innerHTML = html;
    updateActiveDot();
  }

  function updateActiveDot() {
    if (!dotsContainer) return;
    var dots = dotsContainer.querySelectorAll('.journal-shelf-dot');
    if (!dots.length) return;
    var scrollRatio =
      grid.scrollLeft / (grid.scrollWidth - grid.clientWidth || 1);
    var activePage = Math.round(scrollRatio * (dots.length - 1));
    dots.forEach(function (dot, i) {
      dot.classList.toggle('active', i === activePage);
    });
  }

  // Tap on dot to scroll
  if (dotsContainer) {
    dotsContainer.addEventListener('click', function (e) {
      var dot = e.target.closest('.journal-shelf-dot');
      if (!dot) return;
      var page = parseInt(dot.getAttribute('data-dot-page'), 10);
      var dots = dotsContainer.querySelectorAll('.journal-shelf-dot');
      var targetScroll =
        (page / (dots.length - 1 || 1)) * (grid.scrollWidth - grid.clientWidth);
      grid.scrollTo({ left: targetScroll, behavior: 'smooth' });
    });
  }

  // Init — hidden class is rendered server-side, just sync dots/arrows
  updateDots();
  updateArrows();

  // Re-check on resize
  window.addEventListener(
    'resize',
    function () {
      updateDots();
      updateArrows();
    },
    { passive: true },
  );
})();

/* ── Journal browser: restore focus after reading list HTMX swap ─ */
document.body.addEventListener('htmx:afterSettle', (event) => {
  const searchInput = document.getElementById('reading-list-search');
  if (
    searchInput &&
    event.target.id === 'journal-browser-results' &&
    searchInput.value
  ) {
    searchInput.focus();
    // Move cursor to end of input
    const len = searchInput.value.length;
    searchInput.setSelectionRange(len, len);
  }
});

/* ── Dot indicators: reading list button + nav link ──────── */
(function () {
  var DOT_KEY = 'jw_reading_list_dot';

  function showDots() {
    var rlDot = document.getElementById('reading-list-dot');
    var navDot = document.getElementById('nav-journals-dot');
    if (rlDot) rlDot.classList.remove('d-none');
    if (navDot) navDot.classList.remove('d-none');
    document.querySelectorAll('.jw-reading-list-dot').forEach(function (d) {
      d.classList.remove('d-none');
    });
  }

  function hideDots() {
    var rlDot = document.getElementById('reading-list-dot');
    var navDot = document.getElementById('nav-journals-dot');
    if (rlDot) rlDot.classList.add('d-none');
    if (navDot) navDot.classList.add('d-none');
    document.querySelectorAll('.jw-reading-list-dot').forEach(function (d) {
      d.classList.add('d-none');
    });
  }

  // On star change, persist dot, show it, and update star count badges
  document.body.addEventListener('starChanged', function () {
    sessionStorage.setItem(DOT_KEY, '1');
    showDots();
  });

  // Update star count badges after HTMX swaps
  document.body.addEventListener('htmx:afterSettle', function (e) {
    var target = e.detail.elt || e.target;
    var articleId;
    var count;

    // Find star count data from swapped content (data-star-count attr or badge)
    var starSource =
      target.matches && target.matches('[data-star-count]')
        ? target
        : target.querySelector && target.querySelector('[data-star-count]');
    if (starSource && starSource.dataset.articleId) {
      articleId = starSource.dataset.articleId;
      count = Number(starSource.dataset.starCount) || 0;
    }
    if (!articleId) {
      var swappedBadge =
        target.querySelector && target.querySelector('.jw-star-count-badge');
      if (swappedBadge && swappedBadge.dataset.articleId) {
        articleId = swappedBadge.dataset.articleId;
        var val = swappedBadge.querySelector('.jw-star-count-value');
        count = val ? Number(val.textContent) || 0 : 0;
      }
    }

    if (!articleId) return;

    document
      .querySelectorAll(
        '.jw-star-count-badge[data-article-id="' + articleId + '"]',
      )
      .forEach(function (badge) {
        // Skip badges inside the swapped content (already correct)
        if (target.contains && target.contains(badge)) return;
        var val = badge.querySelector('.jw-star-count-value');
        if (val) val.textContent = count;
        badge.classList.toggle('d-none', count === 0);
      });
  });

  // On page load, restore dot state from session
  if (sessionStorage.getItem(DOT_KEY)) {
    showDots();
  }

  // Clear dots when reading list is viewed (any reading list button)
  function clearDotsOnReadingListClick() {
    sessionStorage.removeItem(DOT_KEY);
    hideDots();
  }

  var rlBtn = document.getElementById('reading-list-btn');
  if (rlBtn) {
    rlBtn.addEventListener('click', clearDotsOnReadingListClick);
  }

  // Also clear dots when any bottom/desktop nav reading list button is clicked
  document.addEventListener('click', function (e) {
    var btn = e.target.closest('[data-journal-nav="reading_list"]');
    if (btn) clearDotsOnReadingListClick();
  });
})();

/* ── Nav-scroller edge fades ──────────────────────────────── */
(function () {
  const scroller = document.querySelector('.nav-scroller');
  if (!scroller) return;
  const nav = scroller.querySelector('.nav');
  if (!nav) return;

  function updateFades() {
    const sl = nav.scrollLeft;
    const maxScroll = nav.scrollWidth - nav.clientWidth;
    if (maxScroll <= 0) {
      scroller.removeAttribute('data-fade-left');
      scroller.removeAttribute('data-fade-right');
      return;
    }
    if (sl > 2) scroller.setAttribute('data-fade-left', '');
    else scroller.removeAttribute('data-fade-left');
    if (sl < maxScroll - 2) scroller.setAttribute('data-fade-right', '');
    else scroller.removeAttribute('data-fade-right');
  }

  nav.addEventListener('scroll', updateFades, { passive: true });
  window.addEventListener('resize', updateFades, { passive: true });
  updateFades();
})();

/* ── Journal TOC scroll fades ────────────────────────────── */
(function () {
  function initTocFades() {
    var wrapper = document.querySelector('.journal-toc-wrapper');
    var toc = document.querySelector('.journal-toc');
    if (!wrapper || !toc) return;

    function update() {
      var sl = toc.scrollLeft;
      var max = toc.scrollWidth - toc.clientWidth;
      if (max <= 0) {
        wrapper.removeAttribute('data-fade-left');
        wrapper.removeAttribute('data-fade-right');
        return;
      }
      if (sl > 2) wrapper.setAttribute('data-fade-left', '');
      else wrapper.removeAttribute('data-fade-left');
      if (sl < max - 2) wrapper.setAttribute('data-fade-right', '');
      else wrapper.removeAttribute('data-fade-right');
    }

    toc.addEventListener('scroll', update, { passive: true });
    window.addEventListener('resize', update, { passive: true });
    update();
  }

  initTocFades();
  document.addEventListener('htmx:afterSettle', function (e) {
    if (e.detail.target && e.detail.target.id === 'journal-browser-results') {
      initTocFades();
    }
  });
})();

/* ── Site-wide back to top ────────────────────────────────── */
(function () {
  const allBtns = document.querySelectorAll('.js-back-to-top');
  if (!allBtns.length) return;

  // Desktop FAB visibility toggle
  const fab = document.querySelector('.back-to-top-fab');
  // Mobile toolbar button
  const mobileBtn = document.querySelector(
    '.sticky-mobile-toolbar .js-back-to-top',
  );
  const threshold = 300;
  let visible = false;

  let modalOpen = false;

  function updateVisibility() {
    var show = visible && !modalOpen;
    if (fab) fab.classList.toggle('back-to-top-fab--visible', show);
    if (mobileBtn) mobileBtn.style.visibility = show ? 'visible' : 'hidden';
  }

  window.addEventListener(
    'scroll',
    () => {
      const shouldShow = window.scrollY > threshold;
      if (shouldShow !== visible) {
        visible = shouldShow;
        updateVisibility();
      }
    },
    { passive: true },
  );

  document.addEventListener('show.bs.modal', function () {
    modalOpen = true;
    updateVisibility();
  });
  document.addEventListener('hidden.bs.modal', function () {
    modalOpen = false;
    updateVisibility();
  });

  allBtns.forEach((btn) => {
    btn.addEventListener('click', (e) => {
      e.preventDefault();
      window.scrollTo({ top: 0, behavior: 'smooth' });
    });
  });
})();

/* ── Truncated abstract expand/collapse ──────────────────── */
(function () {
  // Toggle expand/collapse on click
  document.addEventListener('click', function (e) {
    var btn = e.target.closest('.jw-abstract-clamp__toggle');
    if (!btn) return;
    var container = btn.closest('.jw-abstract-clamp');
    if (container) {
      container.classList.toggle('jw-abstract-clamp--expanded');
    }
  });

  // Hide "More" button when abstract isn't actually clamped
  function hideUnnecessaryToggles(root) {
    var clamps = (root || document).querySelectorAll('.jw-abstract-clamp');
    clamps.forEach(function (el) {
      var text = el.querySelector('.jw-abstract-clamp__preview');
      var btn = el.querySelector('.jw-abstract-clamp__toggle');
      if (text && btn) {
        btn.style.display =
          text.scrollHeight > text.clientHeight + 1 ? '' : 'none';
      }
    });
  }

  // Run on initial load and after HTMX swaps
  hideUnnecessaryToggles();
  document.body.addEventListener('htmx:afterSettle', function (e) {
    hideUnnecessaryToggles(e.target);
  });
})();

/* ── CPD tracking toggle → sync body attribute ──────────── */
(function () {
  document.body.addEventListener('cpdTrackingChanged', function () {
    var checkbox = document.getElementById('cpd-tracking-toggle');
    if (checkbox && checkbox.checked) {
      document.body.setAttribute('data-cpd-tracking-enabled', '');
    } else {
      document.body.removeAttribute('data-cpd-tracking-enabled');
    }
  });
})();

/* ── Full-text click checkmarks (all users) ──────────────── */
(function () {
  // Track IDs clicked in this page session (for instant feedback before next server render)
  var clickedThisSession = new Set();

  function addFulltextCheck(container) {
    if (container.querySelector('.jw-fulltext-check')) return;
    var svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.setAttribute('class', 'bi me-1 jw-fulltext-check');
    svg.setAttribute('width', '0.85em');
    svg.setAttribute('height', '0.85em');
    svg.setAttribute('aria-hidden', 'true');
    var use = document.createElementNS('http://www.w3.org/2000/svg', 'use');
    use.setAttribute('href', '#icon-check');
    svg.appendChild(use);
    container.prepend(svg);
  }

  // Record click + show checkmark immediately (before next server render)
  document.addEventListener('click', function (e) {
    var link = e.target.closest(
      '.jw-action-btn--fulltext[data-cpd-article-id]',
    );
    if (!link) return;

    var articleId = Number(link.dataset.cpdArticleId);
    if (!articleId || clickedThisSession.has(articleId)) return;

    clickedThisSession.add(articleId);

    // Beacon to persist in session/profile
    navigator.sendBeacon(
      '/journals/articles/' + articleId + '/mark-read',
      new Blob([], { type: 'application/json' }),
    );

    // Update all matching buttons on the page
    document
      .querySelectorAll(
        '.jw-action-btn--fulltext[data-cpd-article-id="' + articleId + '"]',
      )
      .forEach(function (el) {
        var label = el.querySelector('.jw-action-btn__label');
        if (label) addFulltextCheck(label);
      });
  });
})();

/* ── Journal browser analytics ───────────────────────────── */
(function () {
  var isJournalPage = !!document.getElementById('journal-shelf-grid');
  if (!isJournalPage) return;

  // Track journal selection via HTMX navigation
  document.body.addEventListener('htmx:afterRequest', function (e) {
    var path = (e.detail.pathInfo && e.detail.pathInfo.requestPath) || '';
    if (path.indexOf('/journals') === -1) return;

    // Journal selection — extract journal param from URL
    var url = e.detail.xhr && e.detail.xhr.responseURL;
    if (!url) return;
    var params = new URL(url).searchParams;
    var journalId = params.get('journal');
    if (journalId) {
      sendAnalyticsEvent({
        event_type: 'journal_select',
        metadata: { journal_id: Number(journalId) },
      });
    }
  });

  // Track full-text clicks in journal browser context
  document.addEventListener('click', function (e) {
    var link = e.target.closest(
      '#journal-article-list [data-cpd-article-id], #journal-reading-list [data-cpd-article-id]',
    );
    if (!link) return;

    var articleId = link.dataset.cpdArticleId;
    sendAnalyticsEvent(
      {
        event_type: 'journal_full_text_click',
        metadata: { article_id: Number(articleId) || 0 },
      },
      { beacon: true },
    );
  });

  // Track star toggles via HTMX
  document.body.addEventListener('htmx:afterRequest', function (e) {
    var path = (e.detail.pathInfo && e.detail.pathInfo.requestPath) || '';
    if (path.indexOf('toggle-star') === -1) return;
    if (e.detail.successful !== true) return;

    var match = path.match(/\/journals\/(\d+)\/toggle-star/);
    var articleId = match ? Number(match[1]) : 0;
    sendAnalyticsEvent({
      event_type: 'journal_star',
      metadata: { article_id: articleId },
    });
  });
})();

/* ── Journal browser sticky nav active state ────────────── */
(function () {
  if (!document.getElementById('journal-shelf-grid')) return;

  function setActiveNav(viewName) {
    document.querySelectorAll('[data-journal-nav]').forEach(function (btn) {
      var activeClass = btn.dataset.navActive;
      var inactiveClass = btn.dataset.navInactive;
      if (btn.dataset.journalNav === viewName) {
        btn.classList.remove(inactiveClass);
        btn.classList.add(activeClass);
      } else {
        btn.classList.remove(activeClass);
        btn.classList.add(inactiveClass);
      }
    });
  }

  // Detect current view from server-rendered data attribute
  function detectCurrentView() {
    var results = document.getElementById('journal-browser-results');
    if (!results) return;
    var marker = results.querySelector('[data-current-view]');
    setActiveNav(marker ? marker.getAttribute('data-current-view') : 'shelf');
  }

  // Track which nav view is currently active
  var currentView = null;

  var origSetActiveNav = setActiveNav;
  setActiveNav = function (viewName) {
    currentView = viewName;
    origSetActiveNav(viewName);
  };

  // Cancel HTMX request when the button is already active and no modal is open
  document.body.addEventListener('htmx:confirm', function (e) {
    var btn = e.target.closest('[data-journal-nav]');
    if (!btn) return;
    var openModal = document.querySelector('.modal.show');
    if (btn.dataset.journalNav === currentView && !openModal) {
      e.preventDefault();
    }
  });

  // Update on nav button click + close any open modal
  document.addEventListener('click', function (e) {
    var btn = e.target.closest('[data-journal-nav]');
    if (!btn) return;
    setActiveNav(btn.dataset.journalNav);

    var openModal = document.querySelector('.modal.show');
    if (openModal) {
      Modal.getInstance(openModal)?.hide();
    }
  });

  // Also detect after any HTMX swap into the results area
  document.body.addEventListener('htmx:afterSettle', function (e) {
    if (e.detail.target && e.detail.target.id === 'journal-browser-results') {
      detectCurrentView();
    }
  });

  // Initial state
  detectCurrentView();
})();

// ── Service Worker Registration ────────────────────────────────────
if ('serviceWorker' in navigator) {
  window.addEventListener('load', function () {
    navigator.serviceWorker.register('/sw.js', { scope: '/' });
  });
}
