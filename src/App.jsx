import { useState, useEffect } from 'react';
import TitleBar from './screens/TitleBar';
import WelcomeScreen from './screens/WelcomeScreen';
import SetupScreen from './screens/SetupScreen';
import ChatScreen from './screens/ChatScreen';
import { config } from './store';

const PHASES = {
  LOADING: 'loading',
  WELCOME: 'welcome',
  SETUP: 'setup',
  MAIN: 'main',
};

export default function App() {
  const [phase, setPhase] = useState(PHASES.LOADING);
  const [welcomeData, setWelcomeData] = useState(null);
  const [configData, setConfigData] = useState(null);

  useEffect(() => {
    (async () => {
      const onboardingDone = await config.get('onboardingComplete');
      const setupDone = await config.get('setupComplete');

      if (onboardingDone && setupDone) {
        const all = await config.getAll();
        setConfigData(all);
        setPhase(PHASES.MAIN);
        // Start Python backend on app launch if already set up
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

    // Start Python backend
    window.electronAPI?.backend?.start();
  };

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

  // Main app — Chat Experience
  return (
    <div className={`app-shell ${configData?.theme === 'dark' ? 'theme-dark' : ''}`}>
      <TitleBar />
      <div className="app-content">
        <ChatScreen
          config={configData}
          onReset={async () => {
            await config.reset();
            setConfigData(null);
            setPhase(PHASES.WELCOME);
          }}
        />
      </div>
    </div>
  );
}
