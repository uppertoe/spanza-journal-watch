const path = require('path');
const BundleTracker = require('webpack-bundle-tracker');
const MiniCssExtractPlugin = require('mini-css-extract-plugin');

module.exports = {
  target: 'web',
  context: path.join(__dirname, '../'),
  entry: {
    public: path.resolve(
      __dirname,
      '../spanza_journal_watch/static/js/public_styles',
    ),
    backend: path.resolve(
      __dirname,
      '../spanza_journal_watch/static/js/backend_styles',
    ),
    project: path.resolve(
      __dirname,
      '../spanza_journal_watch/static/js/project',
    ),
    vendors: path.resolve(
      __dirname,
      '../spanza_journal_watch/static/js/vendors',
    ),
    analytics_backend: path.resolve(
      __dirname,
      '../spanza_journal_watch/static/js/analytics_backend',
    ),
  },
  output: {
    path: path.resolve(
      __dirname,
      '../spanza_journal_watch/static/webpack_bundles/',
    ),
    publicPath: '/static/webpack_bundles/',
    filename: 'js/[name]-[fullhash].js',
    chunkFilename: 'js/[name]-[hash].js',
  },
  plugins: [
    new BundleTracker({
      path: path.resolve(__dirname, '../'),
      filename: 'webpack-stats.json',
    }),
    new MiniCssExtractPlugin({ filename: 'css/[name].[contenthash].css' }),
  ],
  module: {
    rules: [
      // we pass the output from babel loader to react-hot loader
      {
        test: /\.js$/,
        loader: 'babel-loader',
      },
      {
        test: /\.s?css$/i,
        use: [
          MiniCssExtractPlugin.loader,
          'css-loader',
          {
            loader: 'postcss-loader',
            options: {
              postcssOptions: {
                plugins: ['postcss-preset-env', 'autoprefixer', 'pixrem'],
              },
            },
          },
          {
            loader: 'sass-loader',
            options: {
              api: 'modern',
              sassOptions: {
                loadPaths: ['node_modules'],
                quietDeps: true,
                silenceDeprecations: [
                  'legacy-js-api',
                  'import',
                  'global-builtin',
                  'color-functions',
                ],
              },
            },
          },
        ],
      },
    ],
  },
  resolve: {
    modules: ['node_modules'],
    extensions: ['.js', '.jsx'],
  },
};
