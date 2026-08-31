const path = require('path')
const HtmlWebpackPlugin = require('html-webpack-plugin')

// The add-on is loaded by Feishu from a relative path inside the uploaded package,
// so publicPath must stay relative — an absolute '/' would 404 on the CDN.
module.exports = {
  entry: './src/main.js',
  output: {
    path: path.resolve(__dirname, 'dist'),
    filename: 'assets/[name].[contenthash:8].js',
    publicPath: './',
    clean: true,
  },
  plugins: [
    new HtmlWebpackPlugin({
      template: './index.html',
      filename: 'index.html',
    }),
  ],
  module: {
    rules: [
      {
        test: /\.css$/i,
        use: ['style-loader', 'css-loader'],
      },
    ],
  },
  devServer: {
    port: 8080,
    hot: true,
    // The dev page is framed by the Feishu client; without this the browser
    // refuses to display it and the block renders blank.
    headers: { 'Access-Control-Allow-Origin': '*' },
    allowedHosts: 'all',
  },
}
