import React, { useState, useEffect } from 'react';
import { ref, onValue } from "firebase/database";
import { db } from '../firebase';
import './InventoryModal.css';

const RobotStatusModal = ({ isOpen, onClose }) => {
  const [robotData, setRobotData] = useState({});
  const robotIds = [1, 2, 3, 4, 5];

  useEffect(() => {
    if (!isOpen) return;

    const statusRef = ref(db, 'Robot_status/Robot1_status');
    
    const unsubscribe = onValue(statusRef, (snapshot) => {
      const data = snapshot.val();
      if (data) {
        setRobotData(data);
      } else {
        setRobotData({});
      }
    });

    return () => unsubscribe();
  }, [isOpen]);

  // 배터리 잔량에 따른 색상을 결정하는 함수
  const getBatteryColor = (level) => {
    if (level > 70) return '#10b981'; // 초록 (충분)
    if (level > 30) return '#f59e0b'; // 노랑 (보통)
    return '#ef4444'; // 빨강 (부족)
  };

  if (!isOpen) return null;

  return (
    <div className="modal-overlay">
      <div className="modal-content" style={{ maxWidth: '1400px', width: '95%', padding: '40px' }}>
        <div className="modal-header">
          <h2 className="modal-title" style={{ fontSize: '28px' }}>로봇 통합 관제 및 배터리 모니터링</h2>
          <button onClick={onClose} className="close-button" style={{ padding: '10px 25px' }}>닫기</button>
        </div>

        <div className="table-container" style={{ marginTop: '30px', overflowX: 'auto' }}>
          <table className="inventory-table" style={{ width: '100%' }}>
            <thead>
              <tr>
                {robotIds.map((id) => (
                  <th key={`robot-th-${id}`} style={{ fontSize: '20px', padding: '20px' }}>
                    Robot {id}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              <tr>
                {robotIds.map((id) => {
                  const statusValue = robotData[id]?.status || 'OFFLINE';
                  const batteryValue = parseInt(robotData[id]?.battery, 10) || 0;
                  const isOnline = statusValue !== 'OFFLINE';
                  
                  return (
                    <td key={`robot-td-${id}`} style={{ padding: '30px', borderRight: '1px solid #f3f4f6' }}>
                      {/* 로봇 동작 상태 표시 */}
                      <div 
                        style={{ 
                          fontSize: '22px',
                          fontWeight: '800', 
                          color: isOnline ? '#1e40af' : '#9ca3af',
                          marginBottom: '20px'
                        }}
                      >
                        {statusValue}
                      </div>

                      {/* 배터리 레이블 및 수치 */}
                      <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '8px', fontSize: '14px', fontWeight: 'bold' }}>
                        <span>Battery</span>
                        <span style={{ color: getBatteryColor(batteryValue) }}>{batteryValue}%</span>
                      </div>

                      {/* 시각적 배터리 게이지 바 */}
                      <div style={{ 
                        width: '100%', 
                        height: '12px', 
                        backgroundColor: '#e5e7eb', 
                        borderRadius: '6px',
                        overflow: 'hidden'
                      }}>
                        <div style={{ 
                          width: `${batteryValue}%`, 
                          height: '100%', 
                          backgroundColor: getBatteryColor(batteryValue),
                          transition: 'width 0.5s ease-in-out'
                        }}></div>
                      </div>
                    </td>
                  );
                })}
              </tr>
            </tbody>
          </table>
        </div>

        <div style={{ marginTop: '30px', padding: '15px', backgroundColor: '#eff6ff', borderRadius: '12px', textAlign: 'left' }}>
          <p style={{ margin: 0, color: '#1e40af', fontSize: '15px' }}>
            <strong>관제 안내:</strong> 배터리가 30% 이하인 로봇은 <span style={{ color: '#ef4444', fontWeight: 'bold' }}>빨간색</span>으로 표시됩니다. 즉시 충전 구역으로 이동시키십시오.
          </p>
        </div>
      </div>
    </div>
  );
};

export default RobotStatusModal;
