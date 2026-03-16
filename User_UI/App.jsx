import React, { useState, useEffect } from 'react';
import { initializeApp } from 'firebase/app';
import { getDatabase, ref, onValue, push, set, get, child, update} from 'firebase/database';
import './App.css';

/* [수정 사항][일시 : 2026-03-11 12:00]
-IWhub와의 연동을 위해, status를 Order에 추가
-반응형 UI 적용
-장바구니의 물건 수량 조절 기능 추가
-장바구니의 물건 삭제 기능 추가
-db에 상품 이미지 추가
-회원가입, 로그인 기능 추가

[수정 사항][일시 : 2026-03-11 16:00]
-서버에서 불러오는 값을 products/item_##/stock에서 products/item_##/Totalstock으로 변경
-주문 시, 전체 재고에서 주문 수량 만큼 차감하는 재고 업데이트 기능 추가

[수정 사항][일시 : 2026-03-11 16:30]
-주문 조회 기능(버튼, 모달, 서버에서 읽어오기) 추가(로직설명: Order/orderId/ordererId로 필터링)
[수정 사항][일시 : 2026-03-11 17:00]
-주문 시, 로그인 여부(isLoggedIn: true)와 주문자 ID(ordererId)를 함께 기록하도록 로직 수정
-주문 조회 시, 로그인 여부(isLoggedIn: true)와 주문자 ID가 모두 일치하는 데이터만 가져오도록 필터링 강화
[수정 사항 ][일시 : 2026-03-12 16:30]
-모바일 앱 환경을 고려하여 css파일을 수정
-
*/
const firebaseConfig = {
  apiKey: "AIzaSyBd831rtqRM7ZEU7LnY1LRDp6Oin4HhTSo",
  authDomain: "rokeysmarthub.firebaseapp.com",
  databaseURL: "https://rokeysmarthub-default-rtdb.asia-southeast1.firebasedatabase.app",
  projectId: "rokeysmarthub",
  storageBucket: "rokeysmarthub.firebasestorage.app",
  messagingSenderId: "666949886270",
  appId: "1:666949886270:web:7b06fdb086612bbd80cc6f"
};

const app = initializeApp(firebaseConfig);
const db = getDatabase(app);

export default function OrderApp() {
  const [products, setProducts] = useState([]);
  const [cart, setCart] = useState([]);
  const [isCartOpen, setIsCartOpen] = useState(false);
  
  const [authMode, setAuthMode] = useState(null); 
  const [currentUser, setCurrentUser] = useState(null);
  const [myOrders, setMyOrders] = useState([]);
  const [isOrderModalOpen, setIsOrderModalOpen] = useState(false);

  const [authForm, setAuthForm] = useState({ id: '', pw: '', name: '', address: '' });
  const [manualId, setManualId] = useState('');
  const [manualAddress, setManualAddress] = useState('');

  useEffect(() => {
    const productsRef = ref(db, 'products');
    onValue(productsRef, (snapshot) => {
      const data = snapshot.val();
      if (data) {
        setProducts(Object.keys(data).map(key => ({ id: key, ...data[key] })));
      }
    });
  }, []);

  /* [강화된 주문 내역 필터링]
     로그인 여부(isLoggedIn: true)와 주문자 ID가 모두 일치하는 데이터만 가져옵니다. */
  useEffect(() => {
    if (currentUser) {
      const ordersRef = ref(db, 'Order');
      const unsubscribe = onValue(ordersRef, (snapshot) => {
        const data = snapshot.val();
        if (data) {
          const filtered = Object.keys(data)
            .filter(key => 
              data[key].isLoggedIn === true && // 로그인 상태에서 주문했는지 확인
              data[key].ordererId === currentUser.id // 주문자 ID가 일치하는지 확인
            )
            .map(key => ({ orderId: key, ...data[key] }));
          setMyOrders(filtered.reverse());
        } else {
          setMyOrders([]);
        }
      });
      return () => unsubscribe();
    } else {
      setMyOrders([]);
      setIsOrderModalOpen(false);
    }
  }, [currentUser]);

  const handleSignUp = () => {
    const { id, pw, name, address } = authForm;
    if (!id || !pw || !name || !address) return alert('모든 항목을 입력하세요.');
    get(child(ref(db), `Customer/${id}`)).then((snap) => {
      if (snap.exists()) return alert('중복된 ID입니다.');
      set(ref(db, `Customer/${id}`), { pw, name, address }).then(() => {
        alert('가입 완료! 로그인 해주세요.');
        setAuthMode('login');
      });
    });
  };

  const handleLogin = () => {
    const { id, pw } = authForm;
    get(child(ref(db), `Customer/${id}`)).then((snap) => {
      if (snap.exists() && snap.val().pw === pw) {
        setCurrentUser({ id, ...snap.val() });
        setAuthMode(null);
        setAuthForm({ id: '', pw: '', name: '', address: '' });
        alert(`${snap.val().name}님 반갑습니다.`);
      } else {
        alert('정보가 일치하지 않습니다.');
      }
    });
  };

  const addToCart = (product) => {
    const item = cart.find(i => i.id === product.id);
    if (item && item.quantity >= product.Totalstock) return alert('재고 부족');
    if (item) {
      setCart(cart.map(i => i.id === product.id ? { ...i, quantity: i.quantity + 1 } : i));
    } else {
      setCart([...cart, { ...product, quantity: 1, status: '준비 중' }]);
    }
  };

  const updateQuantity = (id, delta) => {
    setCart(cart.map(item => {
      if (item.id === id) {
        const newQty = item.quantity + delta;
        if (newQty > item.Totalstock) return item;
        return newQty >= 1 ? { ...item, quantity: newQty } : item;
      }
      return item;
    }));
  };

  const removeFromCart = (id) => {
    if (window.confirm('삭제하시겠습니까?')) {
      setCart(cart.filter(item => item.id !== id));
    }
  };

  const updateStockAfterOrder = async (orderedItems) => {
    const updates = {};
    for (const item of orderedItems) {
      const productRef = child(ref(db), `products/${item.id}`);
      const snapshot = await get(productRef);
      if (snapshot.exists()) {
        const currentTotalStock = snapshot.val().Totalstock || 0;
        updates[`/products/${item.id}/Totalstock`] = Math.max(0, currentTotalStock - item.quantity);
      }
    }
    return update(ref(db), updates);
  };

  /* [수정된 주문 확정 로직]
     'isLoggedIn' 필드를 추가하여 주문 기록 시 로그인 여부를 명시합니다. */
  const placeOrder = async () => {
    if (cart.length === 0) return;
    const finalId = currentUser ? currentUser.id : manualId;
    const finalAddress = currentUser ? currentUser.address : manualAddress;
    if (!finalId || !finalAddress) return alert('배송 정보를 확인하세요.');

    try {
      const orderRef = push(ref(db, 'Order'));
      await set(orderRef, {
        isLoggedIn: currentUser ? true : false, // 로그인 여부 기록
        ordererId: finalId,
        orderAddress: finalAddress,
        orderTime: new Date().toISOString(),
        items: cart,
        totalPrice: cart.reduce((t, i) => t + (i.price * i.quantity), 0),
        status: '상품 준비 중'
      });
      await updateStockAfterOrder(cart);
      alert('주문 완료!');
      setCart([]); setIsCartOpen(false);
    } catch (error) {
      alert('오류 발생');
    }
  };

  return (
    <div className="app-container">
      <header className="header">
        <div style={{ fontSize: '1.8rem', fontWeight: 'bold', color: '#0073e9' }}>📦 MyShop</div>
        <div style={{ display: 'flex', gap: '15px', alignItems: 'center' }}>
          {currentUser ? (
            <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
              <span><strong>{currentUser.name}</strong>님</span>
              <button onClick={() => setIsOrderModalOpen(true)} style={{ padding: '8px 12px', background: '#333', color: '#fff', border: 'none', borderRadius: '4px', cursor: 'pointer' }}>주문 조회</button>
              <button onClick={() => setCurrentUser(null)} style={{ padding: '8px 12px', background: '#eee', border: 'none', borderRadius: '4px', cursor: 'pointer' }}>로그아웃</button>
            </div>
          ) : (
            <>
              <button onClick={() => setAuthMode('login')} style={{ padding: '8px 15px', background: '#0073e9', color: '#fff', border: 'none', borderRadius: '4px', cursor: 'pointer' }}>로그인</button>
              <button onClick={() => setAuthMode('signup')} style={{ padding: '8px 15px', background: '#333', color: '#fff', border: 'none', borderRadius: '4px', cursor: 'pointer' }}>회원가입</button>
            </>
          )}
          <button onClick={() => setIsCartOpen(true)} style={{ padding: '8px 15px', background: '#fff', border: '1px solid #0073e9', color: '#0073e9', borderRadius: '4px', cursor: 'pointer' }}>
            장바구니({cart.reduce((t, i) => t + i.quantity, 0)})
          </button>
        </div>
      </header>

      <main className="main-content">
        <h2 style={{ marginBottom: '30px' }}>상품 목록</h2>
        <div className="product-grid">
          {products.map(p => (
            <div key={p.id} className="product-card">
              <img src={p.image} alt={p.name} className="product-image" />
              <h4 style={{ margin: '10px 0' }}>{p.name}</h4>
              <p style={{ color: '#ae0000', fontWeight: 'bold' }}>{p.price.toLocaleString()}원</p>
              <p style={{ fontSize: '0.9rem', color: '#888' }}>재고: {p.Totalstock || 0}개</p>
              <button onClick={() => addToCart(p)} disabled={(p.Totalstock || 0) <= 0} style={{ width: '100%', padding: '12px', marginTop: '15px', background: '#0073e9', color: '#fff', border: 'none', borderRadius: '6px', cursor: 'pointer' }}>
                담기
              </button>
            </div>
          ))}
        </div>
      </main>

      {/* 주문 내역 모달 */}
      {isOrderModalOpen && (
        <div className="order-modal-overlay">
          <div className="order-modal">
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', borderBottom: '2px solid #eee', paddingBottom: '15px' }}>
              <h2 style={{ margin: 0 }}>나의 주문 내역</h2>
              <button onClick={() => setIsOrderModalOpen(false)} style={{ background: 'none', border: 'none', fontSize: '28px', cursor: 'pointer' }}>&times;</button>
            </div>
            <div className="order-list-container">
              {myOrders.length === 0 ? (
                <p style={{ textAlign: 'center', color: '#999', marginTop: '50px' }}>회원님께서 주문하신 내역이 없습니다.</p>
              ) : (
                myOrders.map(order => (
                  <div key={order.orderId} className="order-item-card">
                    <div>
                      <div style={{ fontSize: '12px', color: '#666' }}>{new Date(order.orderTime).toLocaleString()}</div>
                      <div style={{ fontWeight: 'bold', margin: '5px 0' }}>주문 ID: {order.orderId.substring(0, 12)}...</div>
                      <div style={{ fontSize: '14px', color: '#333' }}>
                        {order.items[0].name} {order.items.length > 1 ? `외 ${order.items.length - 1}건` : ''}
                      </div>
                    </div>
                    <div className="order-status-badge">{order.status}</div>
                  </div>
                ))
              )}
            </div>
          </div>
        </div>
      )}

      {/* 인증 및 장바구니 모달 */}
      {authMode && (
        <div className="auth-modal-overlay">
          <div className="auth-modal">
            <h2>{authMode === 'login' ? '로그인' : '회원가입'}</h2>
            <input placeholder="ID" value={authForm.id} onChange={e => setAuthForm({...authForm, id: e.target.value})} />
            <input type="password" placeholder="Password" value={authForm.pw} onChange={e => setAuthForm({...authForm, pw: e.target.value})} />
            {authMode === 'signup' && (
              <>
                <input placeholder="이름" value={authForm.name} onChange={e => setAuthForm({...authForm, name: e.target.value})} />
                <input placeholder="주소" value={authForm.address} onChange={e => setAuthForm({...authForm, address: e.target.value})} />
              </>
            )}
            <button onClick={authMode === 'login' ? handleLogin : handleSignUp} style={{ padding: '15px', background: '#0073e9', color: '#fff', border: 'none', borderRadius: '8px', cursor: 'pointer', fontWeight: 'bold' }}>확인</button>
            <button onClick={() => setAuthMode(null)} style={{ background: 'none', border: 'none', color: '#999', cursor: 'pointer', marginTop: '10px' }}>닫기</button>
          </div>
        </div>
      )}

      {isCartOpen && (
        <div className="cart-modal">
          <div style={{ display: 'flex', justifyContent: 'space-between', borderBottom: '1px solid #eee', paddingBottom: '15px' }}>
            <h2 style={{ margin: 0 }}>장바구니</h2>
            <button onClick={() => setIsCartOpen(false)} style={{ background: 'none', border: 'none', fontSize: '28px', cursor: 'pointer' }}>&times;</button>
          </div>
          <div style={{ flex: 1, overflowY: 'auto', margin: '20px 0' }}>
            {cart.map(item => (
              <div key={item.id} style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '15px', borderBottom: '1px solid #f9f9f9', paddingBottom: '10px' }}>
                <div style={{ flex: 1 }}>
                  <h4 style={{ margin: '0 0 5px 0', fontSize: '15px' }}>{item.name}</h4>
                  <div style={{ display: 'flex', gap: '10px', alignItems: 'center' }}>
                    <button className="qty-btn" onClick={() => updateQuantity(item.id, -1)}>-</button>
                    <span style={{ fontWeight: 'bold' }}>{item.quantity}</span>
                    <button className="qty-btn" onClick={() => updateQuantity(item.id, 1)}>+</button>
                    <button onClick={() => removeFromCart(item.id)} style={{ color: 'red', border: 'none', background: 'none', fontSize: '12px', cursor: 'pointer' }}>삭제</button>
                  </div>
                </div>
                <div style={{ textAlign: 'right', fontWeight: 'bold' }}>{(item.price * item.quantity).toLocaleString()}원</div>
              </div>
            ))}
          </div>
          <div style={{ borderTop: '2px solid #333', paddingTop: '20px' }}>
            {currentUser ? (
              <div style={{ padding: '12px', background: '#eef6ff', borderRadius: '6px', marginBottom: '15px', fontSize: '14px' }}>
                <strong>회원 정보 자동 입력</strong><br/>
                ID: {currentUser.id} / 주소: {currentUser.address}
              </div>
            ) : (
              <div style={{ display: 'flex', flexDirection: 'column', gap: '8px', marginBottom: '15px' }}>
                <input placeholder="주문자 ID" value={manualId} onChange={e => setManualId(e.target.value)} style={{ padding: '10px' }} />
                <input placeholder="배송지 주소" value={manualAddress} onChange={e => setManualAddress(e.target.value)} style={{ padding: '10px' }} />
              </div>
            )}
            <h3 style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '20px' }}>
              <span>총액:</span>
              <span style={{ color: '#ae0000' }}>{cart.reduce((t, i) => t + (i.price * i.quantity), 0).toLocaleString()}원</span>
            </h3>
            <button onClick={placeOrder} disabled={cart.length === 0} style={{ width: '100%', padding: '18px', background: '#0073e9', color: '#fff', border: 'none', borderRadius: '8px', fontWeight: 'bold', cursor: 'pointer' }}>주문 확정</button>
          </div>
        </div>
      )}
    </div>
  );
}