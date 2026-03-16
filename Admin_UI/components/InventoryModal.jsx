import React, { useState, useEffect } from 'react';
import { ref, onValue } from "firebase/database";
import { db } from '../firebase';
import './InventoryModal.css';

const InventoryModal = ({ isOpen, onClose }) => {
  const [inventory, setInventory] = useState(null);

  useEffect(() => {
    if (!isOpen) return;

    const productsRef = ref(db, 'products');
    
    const unsubscribe = onValue(productsRef, (snapshot) => {
      const data = snapshot.val();
      if (data) {
        setInventory(data);
      } else {
        setInventory({});
      }
    });

    return () => unsubscribe();
  }, [isOpen]);

  const getSquareClasses = (stockValue) => {
    // 2x2 그리드 내의 DOM 렌더링 순서: 0(좌상단), 1(우상단), 2(좌하단), 3(우하단)
    // 우상단 -> 좌상단 -> 좌하단 -> 우하단 순으로 재고 소진 시 빨간색으로 변경
    return [
      stockValue >= 3 ? 'square-green' : 'square-red', 
      stockValue >= 4 ? 'square-green' : 'square-red', 
      stockValue >= 2 ? 'square-green' : 'square-red', 
      stockValue >= 1 ? 'square-green' : 'square-red'  
    ];
  };

  if (!isOpen) return null;

  const productKeys = inventory ? Object.keys(inventory).sort() : [];

  return (
    <div className="modal-overlay">
      <div className="modal-content">
        <div className="modal-header">
          <h2 className="modal-title">스마트 물류센터 실시간 재고 현황</h2>
          <button 
            onClick={onClose}
            className="close-button"
          >
            닫기
          </button>
        </div>

        <div className="table-container">
          <table className="inventory-table">
            <thead>
              <tr>
                {productKeys.map((key) => {
                  const itemNumMatch = key.match(/\d+/);
                  const itemDisplayNum = itemNumMatch ? parseInt(itemNumMatch[0], 10) : key;
                  
                  return (
                    <th key={`th-${key}`}>
                      상품 {itemDisplayNum}
                    </th>
                  );
                })}
              </tr>
            </thead>
            <tbody>
              <tr>
                {productKeys.map((key) => {
                  const rawStock = inventory[key].stock;
                  const parsedStock = parseInt(rawStock, 10);
                  const validStock = isNaN(parsedStock) ? 0 : Math.min(Math.max(parsedStock, 0), 4);

                  return (
                    <td key={`td-${key}`}>
                      <div className="stock-grid">
                        {getSquareClasses(validStock).map((colorClass, idx) => (
                          <div key={idx} className={`stock-square ${colorClass}`}></div>
                        ))}
                      </div>
                    </td>
                  );
                })}
              </tr>
            </tbody>
          </table>
          
          {productKeys.length === 0 && (
            <div style={{ padding: '20px', textAlign: 'center', color: '#6b7280' }}>
              현재 등록된 상품 데이터가 없습니다.
            </div>
          )}
        </div>
        
        <div className="legend-container">
          <span className="legend-item"><div className="legend-square square-green"></div> 재고 있음 (1칸 = 1개)</span>
          <span className="legend-item"><div className="legend-square square-red"></div> 재고 소진</span>
        </div>
      </div>
    </div>
  );
};

export default InventoryModal;
