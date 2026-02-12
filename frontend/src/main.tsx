import React from 'react';
import ReactDOM from 'react-dom/client';
import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom';

import { GeneratePage } from './pages/GeneratePage';
import { ResultPage } from './pages/ResultPage';
import { StartupPage } from './pages/StartupPage';
import './styles.css';

function AppRouter() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<Navigate to="/startup" replace />} />
        <Route path="/startup" element={<StartupPage />} />
        <Route path="/generate" element={<GeneratePage />} />
        <Route path="/results/:reportId" element={<ResultPage />} />
        <Route path="*" element={<Navigate to="/startup" replace />} />
      </Routes>
    </BrowserRouter>
  );
}

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <AppRouter />
  </React.StrictMode>
);
