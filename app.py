import streamlit as st
import firebase_admin
from firebase_admin import credentials, firestore
import base64
import hashlib
import datetime
import pandas as pd
from geopy.distance import geodesic
from streamlit_js_eval import streamlit_js_eval

# ------------------------------
# FIREBASE INITIALIZATION
# ------------------------------
if not firebase_admin._apps:
    cred = credentials.Certificate("service_account.json")
    firebase_admin.initialize_app(cred)

db = firestore.client()

# ------------------------------
# DEVICE LOCK
# ------------------------------
def get_device_hash():
    info = st.session_state.get("device_info", "default_device")
    return hashlib.sha256(info.encode()).hexdigest()

# ------------------------------
# SUBSCRIPTION CHECK
# ------------------------------
def is_subscription_active(shop_data):
    end = shop_data.get("subscription_ends")
    if end is None:
        return False
    if hasattr(end, "replace"):
        end_naive = end.replace(tzinfo=None)
    else:
        end_naive = end
    return end_naive > datetime.datetime.utcnow()

# ------------------------------
# SAVE ATTENDANCE
# ------------------------------
def save_attendance(shop_id, user_id, lat, lon, distance_m, selfie_b64):
    doc_ref = db.collection("attendance").document()
    doc_ref.set({
        "shop_id": shop_id,
        "user_id": user_id,
        "timestamp": datetime.datetime.utcnow(),
        "lat": lat,
        "lon": lon,
        "distance_m": distance_m,
        "selfie_b64": selfie_b64,
    })

# ------------------------------
# CHECK TODAY ATTENDANCE
# ------------------------------
def has_attended_today(shop_id, user_name):
    today = datetime.datetime.utcnow().date()
    docs = db.collection("attendance") \
             .where("shop_id", "==", shop_id) \
             .where("user_id", "==", user_name) \
             .stream()
    for d in docs:
        ts = d.to_dict()["timestamp"]
        if hasattr(ts, "date"):
            ts_date = ts.date()
        else:
            ts_date = ts.to_pydatetime().date()
        if ts_date == today:
            return True
    return False

# ------------------------------
# EMPLOYEE PAGE
# ------------------------------
def employee_page():
    st.title("Employee Attendance")

    shop_docs = db.collection("shops").get()
    if not shop_docs:
        st.error("No shops available. Ask admin to create one.")
        return

    shop_ids = [d.id for d in shop_docs]
    shop_id = st.selectbox("Select Shop", shop_ids)
    pin = st.text_input("PIN", type="password")
    user_name = st.text_input("Your Name")

    if st.button("Login / Register"):
        doc = db.collection("shops").document(shop_id).get()
        if not doc.exists:
            st.error("Shop does not exist.")
            return
        shop_data = doc.to_dict()
        if pin != str(shop_data.get("pin")):
            st.error("Wrong PIN")
            return

        user_doc_ref = db.collection("users").document(f"{shop_id}::{user_name}")
        user_doc = user_doc_ref.get()
        device_hash = get_device_hash()

        if user_doc.exists:
            user_data = user_doc.to_dict()
            if user_data.get("device_hash") != device_hash:
                st.error("Device mismatch! Ask admin to reset device.")
                return
        else:
            user_doc_ref.set({
                "name": user_name,
                "shop_id": shop_id,
                "device_hash": device_hash
            })

        st.session_state["logged_in"] = True
        st.session_state["shop_id"] = shop_id
        st.session_state["user_name"] = user_name
        st.success("Login successful!")
        attendance_page()

# ------------------------------
# ATTENDANCE PAGE
# ------------------------------
def attendance_page():
    st.subheader("Mark Attendance")
    shop_id = st.session_state["shop_id"]
    user_name = st.session_state["user_name"]
    shop = db.collection("shops").document(shop_id).get().to_dict()

    if not is_subscription_active(shop):
        st.error("Subscription expired — ask admin to renew!")
        return

    if has_attended_today(shop_id, user_name):
        st.info("✅ You have already marked attendance today")
        return

    # ------------------------------
    # AUTO LOCATION
    # ------------------------------
    location = streamlit_js_eval(
        js_expressions="navigator.geolocation.getCurrentPosition(p => ({lat:p.coords.latitude, lon:p.coords.longitude}))"
    )
    if location is None:
        st.warning("Waiting for location permission...")
        return

    lat = location["lat"]
    lon = location["lon"]
    st.write(f"Your location: {lat:.6f}, {lon:.6f}")

    # Optional selfie
    selfie = st.camera_input("Take Selfie (optional)")

    if st.button("Submit Attendance"):
        shop_lat = shop.get("lat")
        shop_lon = shop.get("lon")
        radius = shop.get("radius", 150)
        distance_m = geodesic((lat, lon), (shop_lat, shop_lon)).meters
        selfie_b64 = None
        if selfie:
            selfie_b64 = base64.b64encode(selfie.getvalue()).decode()

        save_attendance(shop_id, user_name, lat, lon, distance_m, selfie_b64)

        if distance_m <= radius:
            st.success("Attendance marked ✅ (within radius)")
        else:
            st.warning("Marked but OUTSIDE radius ⚠️")

    # ------------------------------
    # ATTENDANCE HISTORY
    # ------------------------------
    st.subheader("Your Attendance History")
    docs = db.collection("attendance") \
             .where("user_id", "==", user_name) \
             .where("shop_id", "==", shop_id) \
             .stream()
    rows = [d.to_dict() for d in docs]
    if rows:
        rows.sort(key=lambda x: x["timestamp"], reverse=True)
        df = pd.DataFrame(rows)
        df['timestamp'] = df['timestamp'].apply(lambda x: x.strftime("%Y-%m-%d %H:%M"))
        st.dataframe(df)
    else:
        st.info("No attendance records yet.")

# ------------------------------
# ADMIN PAGE
# ------------------------------
def admin_page():
    st.title("Admin Panel")

    key = st.text_input("Enter Admin Key", type="password")
    if st.button("Login Admin"):
        if key != st.secrets["admin_key"]:
            st.error("Wrong admin key")
            return
        st.session_state["admin"] = True

    if "admin" not in st.session_state:
        return

    tab1, tab2, tab3 = st.tabs(["Create Shop", "Attendance Reports", "Users List"])

    with tab1:
        st.header("Create Shop")
        sid = st.text_input("Shop ID")
        sname = st.text_input("Shop Name")
        spin = st.text_input("Shop PIN")
        slat = st.number_input("Latitude")
        slon = st.number_input("Longitude")
        rad = st.number_input("Radius (meters)", value=150)
        days = st.number_input("Subscription Days", value=30)

        if st.button("Create Shop"):
            db.collection("shops").document(sid).set({
                "name": sname,
                "pin": spin,
                "lat": slat,
                "lon": slon,
                "radius": rad,
                "subscription_ends": datetime.datetime.utcnow() + datetime.timedelta(days=days)
            })
            st.success("Shop created!")

    with tab2:
        st.header("Attendance Reports")
        data = db.collection("attendance").get()
        rows = [d.to_dict() for d in data]
        if rows:
            df = pd.DataFrame(rows)
            df['timestamp'] = df['timestamp'].apply(lambda x: x.strftime("%Y-%m-%d %H:%M"))
            st.dataframe(df)
            st.download_button("Download CSV", df.to_csv(index=False), "attendance.csv", "text/csv")
        else:
            st.info("No attendance yet.")

    with tab3:
        st.header("Users List")
        data = db.collection("users").get()
        rows = [d.to_dict() for d in data]
        if rows:
            st.dataframe(pd.DataFrame(rows))
        else:
            st.info("No users found.")

# ------------------------------
# MAIN ROUTER
# ------------------------------
st.sidebar.title("User Type")
choice = st.sidebar.radio("I am a:", ["Employee", "Admin"])

if choice == "Employee":
    if "logged_in" not in st.session_state:
        employee_page()
    else:
        st.sidebar.button("Logout", on_click=lambda: st.session_state.clear())
        attendance_page()
else:
    admin_page()



