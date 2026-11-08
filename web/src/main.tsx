import React from 'react';
import ReactDOM from 'react-dom/client';
import App from './App';
import { installCrashReporter } from './crashReport';
import './styles.css';

// Необработанные JS-ошибки уходят баг-репортами в /api/feedback ([auto]).
installCrashReporter();

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
