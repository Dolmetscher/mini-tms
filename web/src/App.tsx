import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom';
import Header from './components/Header';
import EditorPage from './pages/EditorPage';
import JobsPage from './pages/JobsPage';
import LoginPage from './pages/LoginPage';
import NotificationsPage from './pages/NotificationsPage';
import ProjectDashboardPage from './pages/ProjectDashboardPage';
import ProjectsPage from './pages/ProjectsPage';
import RatesPage from './pages/RatesPage';
import ReportsPage from './pages/ReportsPage';
import ResourcesPage from './pages/ResourcesPage';
import { AuthProvider } from './state/AuthContext';

function Shell({ children }: { children: React.ReactNode }) {
  return (
    <div className="page">
      <Header />
      {children}
    </div>
  );
}

export default function App() {
  return (
    <AuthProvider>
      <BrowserRouter>
        <Routes>
          <Route path="/login" element={<LoginPage />} />
          <Route path="/jobs" element={<Shell><JobsPage /></Shell>} />
          <Route path="/editor/:id" element={<Shell><EditorPage /></Shell>} />
          <Route path="/projects" element={<Shell><ProjectsPage /></Shell>} />
          <Route path="/projects/:id" element={<Shell><ProjectDashboardPage /></Shell>} />
          <Route path="/resources" element={<Shell><ResourcesPage /></Shell>} />
          <Route path="/rates" element={<Shell><RatesPage /></Shell>} />
          <Route path="/reports" element={<Shell><ReportsPage /></Shell>} />
          <Route path="/notifications" element={<Shell><NotificationsPage /></Shell>} />
          <Route path="*" element={<Navigate to="/jobs" replace />} />
        </Routes>
      </BrowserRouter>
    </AuthProvider>
  );
}
