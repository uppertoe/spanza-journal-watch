const { merge } = require('webpack-merge');
const commonConfig = require('./common.config');

module.exports = merge(commonConfig, {
  mode: 'development',
  // Use a lower-memory source map mode for Docker-based local development.
  devtool: 'eval-cheap-module-source-map',
  watchOptions: {
    ignored: [
      '**/.git/**',
      '**/.venv/**',
      '**/backups/**',
      '**/docs/_build/**',
      '**/tests/regression/snapshots/**',
      '**/spanza_journal_watch/media/**',
    ],
  },
  devServer: {
    client: {
      overlay: {
        errors: true,
        warnings: false,
        runtimeErrors: true,
      },
    },
    port: 3000,
    proxy: [
      {
        context: () => true,
        target: 'http://django:8000',
      },
    ],
    // We need hot=false (Disable HMR) to set liveReload=true
    hot: false,
    liveReload: true,
  },
});
