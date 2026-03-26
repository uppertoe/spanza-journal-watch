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

const cleanupModalArtifacts = () => {
  const anyOpenModal = document.querySelector('.modal.show');
  if (anyOpenModal) return;

  document.querySelectorAll('.modal-backdrop').forEach((el) => el.remove());
  document.body.classList.remove('modal-open');
  document.body.style.removeProperty('overflow');
  document.body.style.removeProperty('padding-right');
};

document.addEventListener('show.bs.modal', (event) => {
  const modal = event.target;
  if (!modal.id || closingModalFromPopStateId) return;

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
    cleanupModalArtifacts();
    return;
  }

  if (closingModalFromPopStateId && closingModalFromPopStateId === modal.id) {
    closingModalFromPopStateId = null;
    cleanupModalArtifacts();
    return;
  }

  const state = window.history.state;
  if (state && state.modalOpen && state.modalId === modal.id) {
    window.history.back();
  }

  cleanupModalArtifacts();
});
