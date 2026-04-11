import {
  Chart,
  BarController,
  LineController,
  BarElement,
  LineElement,
  PointElement,
  CategoryScale,
  LinearScale,
  Filler,
  Legend,
  Tooltip,
} from 'chart.js';
import annotationPlugin from 'chartjs-plugin-annotation';

Chart.register(
  BarController,
  LineController,
  BarElement,
  LineElement,
  PointElement,
  CategoryScale,
  LinearScale,
  Filler,
  Legend,
  Tooltip,
  annotationPlugin,
);
window.Chart = Chart;
