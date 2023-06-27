import { Scrollspy } from 'vendors/bootstrap';
window.bootstrap = { Scrollspy };

htmx.onLoad(function (content) {
  var scrollSpy = new bootstrap.ScrollSpy(content, {
    target: '#contents-list-group',
  });
});
