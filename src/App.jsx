import { useState, useEffect } from 'react';
import TitleBar from './screens/TitleBar';
import WelcomeScreen from './screens/WelcomeScreen';
import SetupScreen from './screens/SetupScreen';
import ChatScreen from './screens/ChatScreen';
import LocalAIScreen from './screens/LocalAIScreen';
import MemoryScreen from './screens/MemoryScreen';
import TasksScreen from './screens/TasksScreen';
import IntegrationsScreen from './screens/IntegrationsScreen';
import AutomationsScreen from './screens/AutomationsScreen';
import PrivacyScreen from './screens/PrivacyScreen';
import SettingsScreen from './screens/SettingsScreen';
import SidebarNavigation from './components/SidebarNavigation';
import { config } from './store';

const PHASES = {
  LOADING: 'loading',
  WELCOME: 'welcome',
  SETUP: 'setup',
  MAIN: 'main',
};

const BACKEND = {
  DISCONNECTED: 'disconnected',
  CONNECTING: 'connecting',
  MODEL_LOADING: 'model_loading',
  READY: 'ready',
  ERROR: 'error',
};

export default function App() {
  const [phase, setPhase] = useState(PHASES.LOADING);
  const [welcomeData, setWelcomeData] = useState(null);
  const [configData, setConfigData] = useState(null);
  const [activeScreen, setActiveScreen] = useState('chat');
  const [backendStatus, setBackendStatus] = useState(BACKEND.DISCONNECTED);
  const [modelAvailable, setModelAvailable] = useState(false);

  useEffect(() => {
    (async () => {
      const onboardingDone = await config.get('onboardingComplete');
      const setupDone = await config.get('setupComplete');

      if (onboardingDone && setupDone) {
        const all = await config.getAll();
        setConfigData(all);
        setPhase(PHASES.MAIN);
        window.electronAPI?.backend?.start();
      } else if (onboardingDone) {
        setPhase(PHASES.SETUP);
      } else {
        setPhase(PHASES.WELCOME);
      }
    })();
  }, []);

  const handleWelcomeComplete = async (data) => {
    setWelcomeData(data);
    await config.set('model', data.model);
    await config.set('dataLocation', data.dataLocation);
    await config.set('onboardingComplete', true);
    setPhase(PHASES.SETUP);
  };

  const handleSetupComplete = async (data) => {
    await config.set('userName', data.userName);
    await config.set('assistantName', data.assistantName);
    await config.set('language', data.language);
    await config.set('theme', data.theme);
    await config.set('model', data.model);
    await config.set('ai_preference', data.model === 'bring' ? 'local' : 'local');
    await config.set('setupComplete', true);

    const all = await config.getAll();
    setConfigData(all);
    setPhase(PHASES.MAIN);

    window.electronAPI?.backend?.start();
  };

  const handleBackendStatus = (status) => setBackendStatus(status);
  const handleModelAvailable = (available) => setModelAvailable(available);

  if (phase === PHASES.LOADING) {
    return (
      <div className="app-shell">
        <TitleBar />
        <div className="app-content">
          <div className="screen">
            <div className="screen-content" style={{ animationDelay: '0s' }}>
              <div className="spinner" />
            </div>
          </div>
        </div>
      </div>
    );
  }

  if (phase === PHASES.WELCOME) {
    return (
      <div className="app-shell">
        <TitleBar />
        <div className="app-content">
          <WelcomeScreen onComplete={handleWelcomeComplete} />
        </div>
      </div>
    );
  }

  if (phase === PHASES.SETUP) {
    return (
      <div className="app-shell">
        <TitleBar />
        <div className="app-content">
          <SetupScreen onComplete={handleSetupComplete} />
        </div>
      </div>
    );
  }

  const userName = configData?.userName || 'User';
  const assistantName = configData?.assistantName || 'Luna';

  const renderScreen = () => {
    switch (activeScreen) {
      case 'chat':
        return (
          <ChatScreen
            config={configData}
            onReset={async () => {
              await config.reset();
              setConfigData(null);
              setPhase(PHASES.WELCOME);
            }}
            onBackendStatus={handleBackendStatus}
            onModelAvailable={handleModelAvailable}
          />
        );
      case 'ai':
        return <LocalAIScreen config={configData} />;
      case 'memory':
        return <MemoryScreen config={configData} />;
      case 'tasks':
        return <TasksScreen config={configData} />;
      case 'integrations':
        return <IntegrationsScreen config={configData} />;
      case 'automations':
        return <AutomationsScreen config={configData} />;
      case 'privacy':
        return <PrivacyScreen config={configData} />;
      case 'settings':
        return (
          <SettingsScreen
            config={configData}
            onReset={async () => {
              await config.reset();
              setConfigData(null);
              setPhase(PHASES.WELCOME);
            }}
          />
        );
      default:
        return <ChatScreen config={configData} onReset={() => {}} />;
    }
  };

  return (
    <div className={`app-shell ${configData?.theme === 'dark' ? 'theme-dark' : ''}`}>
      <TitleBar />
      <div className="app-content app-content-nav">
        <SidebarNavigation
          activeScreen={activeScreen}
          onNavigate={setActiveScreen}
          userName={userName}
          assistantName={assistantName}
          onReset={async () => {
            await config.reset();
            setConfigData(null);
            setPhase(PHASES.WELCOME);
          }}
          backendStatus={backendStatus}
          modelAvailable={modelAvailable}
        />
        {renderScreen()}
      </div>
    </div>
  );
}
