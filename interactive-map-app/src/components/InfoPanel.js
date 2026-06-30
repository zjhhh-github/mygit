import React from 'react';
import './InfoPanel.css';

const InfoPanel = ({ region, onClose, markers }) => {
  // 根据区域类型显示不同的信息
  const renderRegionDetails = () => {
    if (!region) return null;

    return (
      <div className="region-details">
        <h3>{region.name}</h3>
        <div className="region-info">
          <p><strong>名称:</strong> {region.name}</p>
          
          {/* 如果是国家层面的数据 */}
          {region.NAME && <p><strong>国家:</strong> {region.NAME}</p>}
          
          {/* 如果是中国省级数据 */}
          {region.admin && <p><strong>行政区:</strong> {region.admin}</p>}
          
          {/* 如果提供了人数信息 */}
          {region.population && <p><strong>人数:</strong> {region.population}</p>}
          
          {/* 如果提供了面积信息 */}
          {region.area && <p><strong>面积:</strong> {region.area} 平方公里</p>}
          
          {/* 如果是市级数据，显示城市特有信息 */}
          {markers && markers.length > 0 && (
            <div className="markers-section">
              <h4>标注信息</h4>
              {markers.map((marker, index) => (
                <div key={index} className="marker-item">
                  <p><strong>人员 {index + 1}:</strong> {marker.personName || '未知姓名'}</p>
                  <p><strong>地址:</strong> {marker.address || '未提供地址'}</p>
                  <p><strong>坐标:</strong> [{marker.lat.toFixed(6)}, {marker.lng.toFixed(6)}]</p>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    );
  };

  return (
    <div className="info-panel">
      <div className="panel-header">
        <h3>区域详情</h3>
        <button className="close-btn" onClick={onClose}>×</button>
      </div>
      {renderRegionDetails()}
    </div>
  );
};

export default InfoPanel;