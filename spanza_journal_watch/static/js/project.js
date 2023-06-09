import '../sass/project.scss';

/* Project specific Javascript goes here. */
import '../js/htmx.js';

import { Popover, Dropdown, ScrollSpy } from 'bootstrap';
import { Popper } from 'bootstrap/dist/js/bootstrap.bundle';

const popoverTriggerList = document.querySelectorAll(
  '[data-bs-toggle="popover"]',
);

const popoverList = [...popoverTriggerList].map(
  (popoverTriggerEl) => new Popover(popoverTriggerEl),
);

var scrollSpy = new ScrollSpy(document.body, {
  target: '#contents-list-group',
});
