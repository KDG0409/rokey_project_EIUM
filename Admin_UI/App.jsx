import React, { useState } from 'react';
import InventoryModal from './components/InventoryModal';
import RobotStatusModal from './components/RobotStatusModal';
import './App.css';

function App() {
  const [isInventoryOpen, setIsInventoryOpen] = useState(false);
  const [isRobotOpen, setIsRobotOpen] = useState(false);

  return (
    <div className="app-container">
      {/* 배경 장식 요소 */}
      <div className="bg-decoration circle-1"></div>
      <div className="bg-decoration circle-2"></div>

      <header className="app-header">
        <div className="header-badge">Real-time Management</div>
        <h1 className="app-title">스마트 물류센터 관제 시스템</h1>
        <p className="app-subtitle">통합 대시보드에서 실시간 재고와 로봇 상태를 제어하십시오.</p>
      </header>
      
      <main className="dashboard-grid">
        <div className="card-item" onClick={() => setIsInventoryOpen(true)}>
          <div className="card-icon">📦</div>
          <div className="card-info">
            <h3>재고 현황 확인</h3>
            <p>실시간 상품 수량 및 창고 상태 점검</p>
          </div>
          <button className="card-button">모달 열기</button>
        </div>

        <div className="card-item robot-card" onClick={() => setIsRobotOpen(true)}>
          <div className="card-icon">🤖</div>
          <div className="card-info">
            <h3>로봇 상태 확인</h3>
            <p>5대 로봇의 동작 상태 및 배터리 관제</p>
          </div>
          <button className="card-button">모달 열기</button>
        </div>
      </main>

      <footer className="app-footer">
        © 2026 Smart Logistics Center. All rights reserved.
      </footer>

      <InventoryModal 
        isOpen={isInventoryOpen} 
        onClose={() => setIsInventoryOpen(false)} 
      />

      <RobotStatusModal 
        isOpen={isRobotOpen} 
        onClose={() => setIsRobotOpen(false)} 
      />
    </div>
  );
}

export default App;
