export default function TitleBar() {
  return (
    <div className="titlebar">
      <span className="titlebar-title">desktop companion</span>
      <div className="titlebar-controls">
        <button
          className="titlebar-btn btn-minimize"
          onClick={() => window.electronAPI?.minimize()}
        />
        <button
          className="titlebar-btn btn-maximize"
          onClick={() => window.electronAPI?.maximize()}
        />
        <button
          className="titlebar-btn btn-close"
          onClick={() => window.electronAPI?.close()}
        />
      </div>
    </div>
  );
}
