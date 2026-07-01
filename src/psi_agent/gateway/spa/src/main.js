import { createApp } from 'vue'
import App from './App.vue'
import 'material-symbols/outlined.css'
import 'katex/dist/katex.min.css'
import './styles/tokens.css'
import './styles/components.css'
import './styles/layout.css'

const app = createApp(App)
app.directive('focus', { mounted(el) { el.focus() } })
app.mount('#app')
