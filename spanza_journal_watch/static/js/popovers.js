/*import * as bootstrap from 'bootstrap';*/
/*window.Popover = bootstrap.Popover;*/

import { Alert, Button, Collapse, Tooltip, Popover } from 'vendors/bootstrap';

window.bootstrap = {
  Alert,
  Button,
  Collapse,
  Tooltip,
  Popover,
};
const popoverTriggerList = document.querySelectorAll(
  '[data-bs-toggle="popover"]',
);
const popoverList = [...popoverTriggerList].map(
  (popoverTriggerEl) => new bootstrap.Popover(popoverTriggerEl),
);
