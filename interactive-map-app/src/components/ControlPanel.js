import React from 'react';
import './ControlPanel.css';

const ControlPanel = ({ 
  viewLevel, 
  setViewLevel, 
  setChinaProvince, 
  selectedRegion,
  onAddMarker,
  addingMarker
}) => {
  const handleBackToWorld = () => {
    setViewLevel('world');
    setChinaProvince(null);
  };

  const handleBackToChina = () => {
    setViewLevel('china-province');
    setChinaProvince(null);
  };

  const getViewLabel = () => {
    switch(viewLevel) {
      case 'world': return '世界地图';
      case 'china-province': return '中国省级地图';
      case 'china-city': return '中国市级地图';
      default: return '地图';
    }
  };

  return (
    <div className="navigation">
      <h3>{getViewLabel()}</h3>
      
      {(viewLevel === 'china-province' || viewLevel === 'china-city') && (
        <button onClick={handleBackToWorld}>
          返回世界地图
        </button>
      )}
      
      {viewLevel === 'china-city' && (
        <button onClick={handleBackToChina}>
          返回省级地图
        </button>
      )}
      
      <button 
        onClick={onAddMarker}
        style={{
          backgroundColor: addingMarker ? '#f57c00' : '#4caf50',
          marginTop: '10px'
        }}
      >
        {addingMarker ? '取消添加标记' : '添加标记'}
      </button>
      
      {selectedRegion && (
        <div className="selected-region-info">
          <p><strong>当前选中:</strong> {selectedRegion.name}</p>
        </div>
      )}
    </div>
  );
};

export default ControlPanel;