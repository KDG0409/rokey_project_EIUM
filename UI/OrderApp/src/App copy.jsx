// OrderApp.jsx

import React, { useState, useEffect } from 'react';
import { initializeApp } from 'firebase/app';
import { getDatabase, ref, onValue, push, set } from 'firebase/database';
import * as ROSLIB from 'roslib';
import './App.css';

// Firebase 프로젝트 설정 (본인의 정보로 반드시 교체해야 합니다)
const firebaseConfig = {
apiKey: "AIzaSyBd831rtqRM7ZEU7LnY1LRDp6Oin4HhTSo",
authDomain: "rokeysmarthub.firebaseapp.com",
databaseURL: "https://rokeysmarthub-default-rtdb.asia-southeast1.firebasedatabase.app",
projectId: "rokeysmarthub",
storageBucket: "rokeysmarthub.firebasestorage.app",
messagingSenderId: "666949886270",
appId: "1:666949886270:web:7b06fdb086612bbd80cc6f"
};

// Firebase 인스턴스 초기화
const app = initializeApp(firebaseConfig);
const db = getDatabase(app);

export default function OrderApp() {
  const [products, setProducts] = useState([]);
  const [cart, setCart] = useState([]);
  const [isCartOpen, setIsCartOpen] = useState(false);
  
  // 주문용 추가 정보 상태
  const [ordererId, setOrdererId] = useState('');
  const [orderAddress, setOrderAddress] = useState('');

  const [ros, setRos] = useState(null);
  const [rosConnected, setRosConnected] = useState(false);

  // Firebase 실시간 데이터베이스 연동 (상품 목록 가져오기)
  useEffect(() => {
    const rosInstance = new ROSLIB.Ros({
      url: 'ws://localhost:9090' 
    });

    rosInstance.on('connection', () => {
      console.log('ROS Bridge에 연결되었습니다. 🚀');
      setRosConnected(true);
    });

    rosInstance.on('error', (error) => {
      console.error('ROS Bridge 연결 에러:', error);
    });

    rosInstance.on('close', () => {
      console.log('ROS Bridge 연결이 끊어졌습니다.');
      setRosConnected(false);
    });

    setRos(rosInstance);

    // 상품 데이터가 저장된 'products' 경로를 참조합니다.
    const productsRef = ref(db, 'products');
    
    // onValue를 통해 서버의 데이터 변경을 실시간으로 감지합니다.
    const unsubscribe = onValue(productsRef, (snapshot) => {
      const data = snapshot.val();
      if (data) {
        // Firebase 객체 데이터를 리액트 렌더링에 적합한 배열 형태로 변환합니다.
        const productList = Object.keys(data).map(key => ({
          id: key,
          ...data[key]
        }));
        setProducts(productList);
      } else {
        setProducts([]);
      }
    });

    // 컴포넌트 언마운트 시 메모리 누수를 막기 위해 리스너를 해제합니다.
    return () => {
      unsubscribe();
      // if (rosInstance) rosInstance.close();
    };
  }, []);

  // 장바구니 담기 (로컬 상태만 업데이트, 재고는 서버 기준 검증)
  const addToCart = (product) => {
    const existingItem = cart.find(item => item.id === product.id);
    const currentQuantity = existingItem ? existingItem.quantity : 0;

    // 현재 장바구니에 담긴 수량이 서버의 실시간 재고 이상인지 철저히 검증합니다.
    if (currentQuantity >= product.stock) {
      alert('현재 남은 재고 이상으로 장바구니에 담을 수 없습니다.');
      return;
    }

    if (existingItem) {
      setCart(cart.map(item => 
        item.id === product.id ? { ...item, quantity: item.quantity + 1 } : item
      ));
    } else {
      setCart([...cart, { ...product, quantity: 1 }]);
    }
  };

  // 최종 주문 처리 (Firebase 서버 'Order' 경로에 데이터 Push)
  const placeOrder = () => {
    if (cart.length === 0) return;
    if (!ordererId.trim() || !orderAddress.trim()) {
      alert('주문자 ID와 주문 주소를 정확히 모두 입력해주세요.');
      return;
    }

    // --- ROS 토픽 전송 로직 ---
    if (ros && rosConnected) {
      const orderTopic = new ROSLIB.Topic({
        ros: ros,
        name: '/order',
        messageType: 'std_msgs/String'
      });

      const rosPayload = {};
      cart.forEach(item => {
        rosPayload[item.id] = item.quantity;
      });

      const message = new ROSLIB.Message({
        data: JSON.stringify(rosPayload)
      });

      orderTopic.publish(message);
      console.log('ROS로 전송 성공:', rosPayload);
    } else {
      alert('ROS 서버(로봇)와 연결되어 있지 않습니다. 주문 내역은 DB에만 저장됩니다.');
    }

    // 서버의 'Order' 경로를 참조하여 고유 키와 함께 새로운 데이터를 밀어넣습니다.
    const orderRef = ref(db, 'Order');
    const newOrderRef = push(orderRef);
    
    const orderData = {
      ordererId: ordererId,
      orderAddress: orderAddress,
      orderTime: new Date().toISOString(), // 현재 시간을 ISO 문자열로 저장
      items: cart,
      totalPrice: totalCartPrice
    };

    // 서버로 데이터 전송을 시도합니다.
    set(newOrderRef, orderData)
      .then(() => {
        alert('주문이 성공적으로 서버에 등록되었습니다!');
        // 주문 성공 시 로컬 장바구니 및 입력 폼 상태를 깨끗하게 초기화합니다.
        setCart([]); 
        setOrdererId('');
        setOrderAddress('');
        setIsCartOpen(false);
      })
      .catch((error) => {
        alert('주문 처리 중 네트워크 오류가 발생했습니다: ' + error.message);
      });
  };

  const totalCartPrice = cart.reduce((total, item) => total + (item.price * item.quantity), 0);
  const totalCartItems = cart.reduce((total, item) => total + item.quantity, 0);

  return (
    <div className="app-container">
      {/* 헤더 영역 */}
      <header className="header">
        <div style={{ fontSize: '24px', fontWeight: 'bold', color: '#0073e9' }}>
          📦 MyShop
        </div>
        <button 
          onClick={() => setIsCartOpen(!isCartOpen)}
          style={{ padding: '10px 20px', fontSize: '16px', cursor: 'pointer', backgroundColor: '#fff', border: '1px solid #ccc', borderRadius: '4px' }}
        >
          장바구니 🛒 ({totalCartItems})
        </button>
      </header>

      {/* 메인 상품 영역 */}
      <main className="main-content">
        {/* 사이드바 추가 (원하는 내용으로 채우세요) */}
        <aside className="sidebar">
          <h3>카테고리</h3>
          <ul>
            <li>과자/스낵</li>
            <li>초콜릿/캔디</li>
            <li>비스킷/쿠키</li>
          </ul>
        </aside>

        {/* 기존 상품 목록 영역 */}
        <section className="product-content">
          <h2>추천 과자 목록</h2>
          {/* ... 기존 products.map 로직 ... */}
        
        {products.length === 0 ? (
          <p style={{ textAlign: 'center', marginTop: '50px', color: '#666' }}>
            서버에서 상품 데이터를 불러오는 중이거나 데이터가 없습니다. Firebase 데이터베이스의 'products' 경로를 확인하십시오.
          </p>
        ) : (
          <div className="product-grid">
            {products.map(product => (
              <div key={product.id} className="product-card">
                <img src={product.image} alt={product.name} className="product-image" />
                <h4 style={{ margin: '10px 0', fontSize: '16px', height: '45px', overflow: 'hidden' }}>{product.name}</h4>
                <p style={{ color: '#ae0000', fontWeight: 'bold', fontSize: '20px', margin: '5px 0' }}>
                  {Number(product.price).toLocaleString()}원
                </p>
                <p style={{ fontSize: '14px', color: product.stock > 0 ? '#555' : 'red' }}>
                  {product.stock > 0 ? `남은 재고: ${product.stock}개` : '현재 품절'}
                </p>
                <button 
                  onClick={() => addToCart(product)}
                  disabled={product.stock <= 0}
                  style={{ width: '100%', padding: '12px', marginTop: '15px', backgroundColor: product.stock > 0 ? '#0073e9' : '#ccc', color: '#fff', fontSize: '15px', fontWeight: 'bold', border: 'none', borderRadius: '4px', cursor: product.stock > 0 ? 'pointer' : 'not-allowed' }}
                >
                  장바구니 담기
                </button>
              </div>
            ))}
          </div>
        )}
        </section>
      </main>

      {/* 반응형 장바구니 모달 */}
      {isCartOpen && (
        <div className="cart-modal">
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', borderBottom: '1px solid #ddd', paddingBottom: '15px' }}>
            <h2 style={{ margin: 0 }}>장바구니</h2>
            <button onClick={() => setIsCartOpen(false)} style={{ background: 'none', border: 'none', fontSize: '24px', cursor: 'pointer' }}>✖</button>
          </div>
          
          <div style={{ flex: 1, overflowY: 'auto', marginTop: '20px' }}>
            {cart.length === 0 ? (
              <p style={{ textAlign: 'center', color: '#777', marginTop: '50px' }}>장바구니가 비어있습니다.</p>
            ) : (
              <>
                <div style={{ marginBottom: '20px' }}>
                  {cart.map(item => (
                    <div key={item.id} style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '15px', paddingBottom: '15px', borderBottom: '1px solid #eee' }}>
                      <div style={{ flex: 1, paddingRight: '10px' }}>
                        <h4 style={{ margin: '0 0 8px 0', fontSize: '15px', wordBreak: 'keep-all' }}>{item.name}</h4>
                        <span style={{ fontSize: '14px', color: '#777' }}>수량: {item.quantity}개</span>
                      </div>
                      <div style={{ fontWeight: 'bold', fontSize: '16px', display: 'flex', alignItems: 'center' }}>
                        {(item.price * item.quantity).toLocaleString()}원
                      </div>
                    </div>
                  ))}
                </div>
                
                {/* 주문 정보 입력 폼 */}
                <div style={{ backgroundColor: '#f9f9f9', padding: '15px', borderRadius: '8px', border: '1px solid #eee' }}>
                  <h4 style={{ margin: '0 0 10px 0' }}>배송 정보 입력</h4>
                  <input 
                    type="text" 
                    placeholder="주문자 ID" 
                    value={ordererId}
                    onChange={(e) => setOrdererId(e.target.value)}
                    style={{ width: '100%', padding: '10px', marginBottom: '10px', border: '1px solid #ccc', borderRadius: '4px', boxSizing: 'border-box' }}
                  />
                  <input 
                    type="text" 
                    placeholder="배송지 주소" 
                    value={orderAddress}
                    onChange={(e) => setOrderAddress(e.target.value)}
                    style={{ width: '100%', padding: '10px', border: '1px solid #ccc', borderRadius: '4px', boxSizing: 'border-box' }}
                  />
                </div>
              </>
            )}
          </div>

          <div style={{ borderTop: '2px solid #333', paddingTop: '20px', marginTop: '15px' }}>
            <h3 style={{ display: 'flex', justifyContent: 'space-between', margin: '0 0 15px 0' }}>
              <span>총 결제금액:</span> 
              <span style={{ color: '#ae0000', fontSize: '24px' }}>{totalCartPrice.toLocaleString()}원</span>
            </h3>
            <button 
              onClick={placeOrder}
              disabled={cart.length === 0}
              style={{ width: '100%', padding: '18px', backgroundColor: cart.length > 0 ? '#0073e9' : '#ccc', color: '#fff', fontSize: '18px', fontWeight: 'bold', border: 'none', borderRadius: '6px', cursor: cart.length > 0 ? 'pointer' : 'not-allowed' }}
            >
              주문 확정 및 데이터 전송
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
