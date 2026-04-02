import '@popperjs/core';
import Collapse from 'bootstrap/js/dist/collapse';
import Dropdown from 'bootstrap/js/dist/dropdown';
import Modal from 'bootstrap/js/dist/modal';
import Offcanvas from 'bootstrap/js/dist/offcanvas';
import Popover from 'bootstrap/js/dist/popover';
import Tab from 'bootstrap/js/dist/tab';
import Toast from 'bootstrap/js/dist/toast';
import htmx from 'htmx.org';
window.htmx = htmx;

window.bootstrap = {
  Collapse,
  Dropdown,
  Modal,
  Offcanvas,
  Popover,
  Tab,
  Toast,
};
