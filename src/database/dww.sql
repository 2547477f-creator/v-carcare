--
-- PostgreSQL database dump
--

\restrict RS5t0xnsIjT5LDNJgdcKxytpdiVdFfc6QfGEdULxWC6XsBea3my4nt7gdcRUFiP

-- Dumped from database version 18.4
-- Dumped by pg_dump version 18.4

-- Started on 2026-07-23 00:01:10

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET transaction_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- TOC entry 222 (class 1259 OID 24602)
-- Name: app_users; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.app_users (
    id bigint NOT NULL,
    username character varying(80) NOT NULL,
    password_hash text NOT NULL,
    role character varying(20) NOT NULL,
    staff_id bigint,
    is_active boolean DEFAULT true NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT app_users_role_check CHECK (((role)::text = ANY ((ARRAY['manager'::character varying, 'staff'::character varying])::text[])))
);


ALTER TABLE public.app_users OWNER TO postgres;

--
-- TOC entry 221 (class 1259 OID 24601)
-- Name: app_users_id_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.app_users_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.app_users_id_seq OWNER TO postgres;

--
-- TOC entry 5181 (class 0 OID 0)
-- Dependencies: 221
-- Name: app_users_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: postgres
--

ALTER SEQUENCE public.app_users_id_seq OWNED BY public.app_users.id;


--
-- TOC entry 224 (class 1259 OID 24627)
-- Name: customers; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.customers (
    id bigint NOT NULL,
    full_name character varying(120),
    phone character varying(30) NOT NULL,
    line_id character varying(80),
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.customers OWNER TO postgres;

--
-- TOC entry 223 (class 1259 OID 24626)
-- Name: customers_id_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.customers_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.customers_id_seq OWNER TO postgres;

--
-- TOC entry 5182 (class 0 OID 0)
-- Dependencies: 223
-- Name: customers_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: postgres
--

ALTER SEQUENCE public.customers_id_seq OWNED BY public.customers.id;


--
-- TOC entry 240 (class 1259 OID 24813)
-- Name: finance_transactions; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.finance_transactions (
    id bigint NOT NULL,
    order_id bigint,
    staff_id bigint,
    transaction_type character varying(20) NOT NULL,
    category character varying(60) NOT NULL,
    description text NOT NULL,
    amount numeric(10,2) NOT NULL,
    occurred_at timestamp with time zone DEFAULT now() NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT finance_transactions_amount_check CHECK ((amount >= (0)::numeric)),
    CONSTRAINT finance_transactions_transaction_type_check CHECK (((transaction_type)::text = ANY ((ARRAY['income'::character varying, 'expense'::character varying])::text[])))
);


ALTER TABLE public.finance_transactions OWNER TO postgres;

--
-- TOC entry 239 (class 1259 OID 24812)
-- Name: finance_transactions_id_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.finance_transactions_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.finance_transactions_id_seq OWNER TO postgres;

--
-- TOC entry 5183 (class 0 OID 0)
-- Dependencies: 239
-- Name: finance_transactions_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: postgres
--

ALTER SEQUENCE public.finance_transactions_id_seq OWNED BY public.finance_transactions.id;


--
-- TOC entry 236 (class 1259 OID 24768)
-- Name: payments; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.payments (
    id bigint NOT NULL,
    order_id bigint NOT NULL,
    method character varying(30) NOT NULL,
    amount numeric(10,2) NOT NULL,
    paid_at timestamp with time zone DEFAULT now() NOT NULL,
    status character varying(30) DEFAULT 'paid'::character varying NOT NULL,
    CONSTRAINT payments_amount_check CHECK ((amount >= (0)::numeric)),
    CONSTRAINT payments_method_check CHECK (((method)::text = ANY ((ARRAY['cash'::character varying, 'transfer'::character varying, 'card'::character varying, 'other'::character varying])::text[]))),
    CONSTRAINT payments_status_check CHECK (((status)::text = ANY ((ARRAY['pending'::character varying, 'paid'::character varying, 'void'::character varying])::text[])))
);


ALTER TABLE public.payments OWNER TO postgres;

--
-- TOC entry 235 (class 1259 OID 24767)
-- Name: payments_id_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.payments_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.payments_id_seq OWNER TO postgres;

--
-- TOC entry 5184 (class 0 OID 0)
-- Dependencies: 235
-- Name: payments_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: postgres
--

ALTER SEQUENCE public.payments_id_seq OWNED BY public.payments.id;


--
-- TOC entry 234 (class 1259 OID 24745)
-- Name: service_order_items; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.service_order_items (
    id bigint NOT NULL,
    order_id bigint NOT NULL,
    service_id bigint,
    service_code character varying(40) NOT NULL,
    service_name character varying(160) NOT NULL,
    price numeric(10,2) NOT NULL,
    CONSTRAINT service_order_items_price_check CHECK ((price >= (0)::numeric))
);


ALTER TABLE public.service_order_items OWNER TO postgres;

--
-- TOC entry 233 (class 1259 OID 24744)
-- Name: service_order_items_id_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.service_order_items_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.service_order_items_id_seq OWNER TO postgres;

--
-- TOC entry 5185 (class 0 OID 0)
-- Dependencies: 233
-- Name: service_order_items_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: postgres
--

ALTER SEQUENCE public.service_order_items_id_seq OWNED BY public.service_order_items.id;


--
-- TOC entry 232 (class 1259 OID 24703)
-- Name: service_orders; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.service_orders (
    id bigint NOT NULL,
    queue_no character varying(30) NOT NULL,
    customer_id bigint NOT NULL,
    vehicle_id bigint NOT NULL,
    status character varying(30) DEFAULT 'pending'::character varying NOT NULL,
    payment_method character varying(30) DEFAULT 'cash'::character varying NOT NULL,
    total_amount numeric(10,2) DEFAULT 0 NOT NULL,
    note text,
    created_by bigint,
    started_at timestamp with time zone,
    completed_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT service_orders_payment_method_check CHECK (((payment_method)::text = ANY ((ARRAY['cash'::character varying, 'transfer'::character varying, 'card'::character varying, 'other'::character varying])::text[]))),
    CONSTRAINT service_orders_status_check CHECK (((status)::text = ANY ((ARRAY['pending'::character varying, 'in_progress'::character varying, 'drying'::character varying, 'ready'::character varying, 'completed'::character varying, 'cancelled'::character varying])::text[])))
);


ALTER TABLE public.service_orders OWNER TO postgres;

--
-- TOC entry 231 (class 1259 OID 24702)
-- Name: service_orders_id_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.service_orders_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.service_orders_id_seq OWNER TO postgres;

--
-- TOC entry 5186 (class 0 OID 0)
-- Dependencies: 231
-- Name: service_orders_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: postgres
--

ALTER SEQUENCE public.service_orders_id_seq OWNED BY public.service_orders.id;


--
-- TOC entry 230 (class 1259 OID 24682)
-- Name: service_prices; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.service_prices (
    id bigint NOT NULL,
    service_id bigint NOT NULL,
    vehicle_category character varying(20) NOT NULL,
    size_code character varying(10) NOT NULL,
    price numeric(10,2) NOT NULL,
    CONSTRAINT service_prices_price_check CHECK ((price >= (0)::numeric)),
    CONSTRAINT service_prices_vehicle_category_check CHECK (((vehicle_category)::text = ANY ((ARRAY['car'::character varying, 'bike'::character varying])::text[])))
);


ALTER TABLE public.service_prices OWNER TO postgres;

--
-- TOC entry 229 (class 1259 OID 24681)
-- Name: service_prices_id_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.service_prices_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.service_prices_id_seq OWNER TO postgres;

--
-- TOC entry 5187 (class 0 OID 0)
-- Dependencies: 229
-- Name: service_prices_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: postgres
--

ALTER SEQUENCE public.service_prices_id_seq OWNED BY public.service_prices.id;


--
-- TOC entry 228 (class 1259 OID 24664)
-- Name: services; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.services (
    id bigint NOT NULL,
    code character varying(40) NOT NULL,
    name character varying(160) NOT NULL,
    category character varying(20) NOT NULL,
    estimated_minutes integer DEFAULT 30 NOT NULL,
    is_active boolean DEFAULT true NOT NULL,
    CONSTRAINT services_category_check CHECK (((category)::text = ANY ((ARRAY['car'::character varying, 'bike'::character varying, 'all'::character varying])::text[])))
);


ALTER TABLE public.services OWNER TO postgres;

--
-- TOC entry 227 (class 1259 OID 24663)
-- Name: services_id_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.services_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.services_id_seq OWNER TO postgres;

--
-- TOC entry 5188 (class 0 OID 0)
-- Dependencies: 227
-- Name: services_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: postgres
--

ALTER SEQUENCE public.services_id_seq OWNED BY public.services.id;


--
-- TOC entry 220 (class 1259 OID 24578)
-- Name: staff; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.staff (
    id bigint NOT NULL,
    employee_code character varying(20) NOT NULL,
    full_name character varying(120) NOT NULL,
    "position" character varying(80) DEFAULT 'Staff'::character varying NOT NULL,
    daily_wage numeric(10,2) DEFAULT 0 NOT NULL,
    pin_hash text,
    is_active boolean DEFAULT true NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.staff OWNER TO postgres;

--
-- TOC entry 238 (class 1259 OID 24791)
-- Name: staff_attendance; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.staff_attendance (
    id bigint NOT NULL,
    staff_id bigint NOT NULL,
    work_date date DEFAULT CURRENT_DATE NOT NULL,
    check_in_at timestamp with time zone DEFAULT now() NOT NULL,
    check_out_at timestamp with time zone,
    method character varying(40) DEFAULT 'pin'::character varying NOT NULL
);


ALTER TABLE public.staff_attendance OWNER TO postgres;

--
-- TOC entry 237 (class 1259 OID 24790)
-- Name: staff_attendance_id_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.staff_attendance_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.staff_attendance_id_seq OWNER TO postgres;

--
-- TOC entry 5189 (class 0 OID 0)
-- Dependencies: 237
-- Name: staff_attendance_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: postgres
--

ALTER SEQUENCE public.staff_attendance_id_seq OWNED BY public.staff_attendance.id;


--
-- TOC entry 219 (class 1259 OID 24577)
-- Name: staff_id_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.staff_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.staff_id_seq OWNER TO postgres;

--
-- TOC entry 5190 (class 0 OID 0)
-- Dependencies: 219
-- Name: staff_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: postgres
--

ALTER SEQUENCE public.staff_id_seq OWNED BY public.staff.id;


--
-- TOC entry 226 (class 1259 OID 24642)
-- Name: vehicles; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.vehicles (
    id bigint NOT NULL,
    customer_id bigint NOT NULL,
    license_plate character varying(40) NOT NULL,
    province character varying(80),
    category character varying(20) NOT NULL,
    size_code character varying(10) NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT vehicles_category_check CHECK (((category)::text = ANY ((ARRAY['car'::character varying, 'bike'::character varying])::text[])))
);


ALTER TABLE public.vehicles OWNER TO postgres;

--
-- TOC entry 225 (class 1259 OID 24641)
-- Name: vehicles_id_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.vehicles_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.vehicles_id_seq OWNER TO postgres;

--
-- TOC entry 5191 (class 0 OID 0)
-- Dependencies: 225
-- Name: vehicles_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: postgres
--

ALTER SEQUENCE public.vehicles_id_seq OWNED BY public.vehicles.id;


--
-- TOC entry 4912 (class 2604 OID 24605)
-- Name: app_users id; Type: DEFAULT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.app_users ALTER COLUMN id SET DEFAULT nextval('public.app_users_id_seq'::regclass);


--
-- TOC entry 4915 (class 2604 OID 24630)
-- Name: customers id; Type: DEFAULT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.customers ALTER COLUMN id SET DEFAULT nextval('public.customers_id_seq'::regclass);


--
-- TOC entry 4938 (class 2604 OID 24816)
-- Name: finance_transactions id; Type: DEFAULT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.finance_transactions ALTER COLUMN id SET DEFAULT nextval('public.finance_transactions_id_seq'::regclass);


--
-- TOC entry 4931 (class 2604 OID 24771)
-- Name: payments id; Type: DEFAULT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.payments ALTER COLUMN id SET DEFAULT nextval('public.payments_id_seq'::regclass);


--
-- TOC entry 4930 (class 2604 OID 24748)
-- Name: service_order_items id; Type: DEFAULT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.service_order_items ALTER COLUMN id SET DEFAULT nextval('public.service_order_items_id_seq'::regclass);


--
-- TOC entry 4924 (class 2604 OID 24706)
-- Name: service_orders id; Type: DEFAULT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.service_orders ALTER COLUMN id SET DEFAULT nextval('public.service_orders_id_seq'::regclass);


--
-- TOC entry 4923 (class 2604 OID 24685)
-- Name: service_prices id; Type: DEFAULT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.service_prices ALTER COLUMN id SET DEFAULT nextval('public.service_prices_id_seq'::regclass);


--
-- TOC entry 4920 (class 2604 OID 24667)
-- Name: services id; Type: DEFAULT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.services ALTER COLUMN id SET DEFAULT nextval('public.services_id_seq'::regclass);


--
-- TOC entry 4906 (class 2604 OID 24581)
-- Name: staff id; Type: DEFAULT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.staff ALTER COLUMN id SET DEFAULT nextval('public.staff_id_seq'::regclass);


--
-- TOC entry 4934 (class 2604 OID 24794)
-- Name: staff_attendance id; Type: DEFAULT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.staff_attendance ALTER COLUMN id SET DEFAULT nextval('public.staff_attendance_id_seq'::regclass);


--
-- TOC entry 4918 (class 2604 OID 24645)
-- Name: vehicles id; Type: DEFAULT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.vehicles ALTER COLUMN id SET DEFAULT nextval('public.vehicles_id_seq'::regclass);


--
-- TOC entry 5157 (class 0 OID 24602)
-- Dependencies: 222
-- Data for Name: app_users; Type: TABLE DATA; Schema: public; Owner: postgres
--

COPY public.app_users (id, username, password_hash, role, staff_id, is_active, created_at) FROM stdin;
1	admin	scrypt:32768:8:1$6VgJ3qwtbQRvDzoT$a31bbb8711592f0cd77a043673a18202c650c00431c2c5ee0352d52c542d5f732fccce18c7e63542543b0dcf5d1e95be2471cfa126ec6fcc9101bc4834a9ba54	manager	\N	t	2026-07-22 22:55:33.206579+07
2	S01	scrypt:32768:8:1$7ZqFrkVBHECkYbKq$534508019b44aa2ec90a28b4fc14de935da51c5c193742c40cc0349555c1abac5e3b3dc915992f5e3cb0675c7a00019895536714332d2600160882c388f8b62c	staff	1	t	2026-07-22 22:55:33.593675+07
3	S02	scrypt:32768:8:1$sW4BPiLHG0Fk36sU$c6bd79aba39300b1d75a82d9e47856739e65f67eae2c1a4126852dbf37a98bfb03aa3e41cb910d3369019b71fa24be70d75e96e3a9cb3e98005dd9eb2286aeda	staff	2	t	2026-07-22 22:55:33.916142+07
4	S03	scrypt:32768:8:1$n5yi5ZFm4SK5v28X$08bae19bd5d848f7d882dfbf6ec451cbe65647d8c6b202f09cf540c1e745d782bde5e103ed8a72c79e67bf3afac5ef651e2ff76a8a2e712c4a4ea9aa190a9ac3	staff	3	t	2026-07-22 22:55:34.123928+07
5	S04	scrypt:32768:8:1$2AARlGOltKNhJSj5$2b7e65e12cf703827cebf117238c826ebefc9119d644c2b77f8e26eee5efbb2f832b249b8dc10efd24958e35cd4dc4de3d04ed235037ecf45fcb95c3b0615926	staff	4	f	2026-07-22 23:09:01.956389+07
\.


--
-- TOC entry 5159 (class 0 OID 24627)
-- Dependencies: 224
-- Data for Name: customers; Type: TABLE DATA; Schema: public; Owner: postgres
--

COPY public.customers (id, full_name, phone, line_id, created_at, updated_at) FROM stdin;
1	\N	dawd	dawd	2026-07-22 22:59:04.673+07	2026-07-22 22:59:04.673+07
2	\N	555555	cxwww	2026-07-22 23:14:12.502188+07	2026-07-22 23:14:12.502188+07
\.


--
-- TOC entry 5175 (class 0 OID 24813)
-- Dependencies: 240
-- Data for Name: finance_transactions; Type: TABLE DATA; Schema: public; Owner: postgres
--

COPY public.finance_transactions (id, order_id, staff_id, transaction_type, category, description, amount, occurred_at, created_at) FROM stdin;
1	1	\N	income	service	Car care service dwd	1180.00	2026-07-22 22:59:04.673+07	2026-07-22 22:59:04.673+07
2	2	\N	income	service	Car care service 191	120.00	2026-07-22 23:14:12.502188+07	2026-07-22 23:14:12.502188+07
\.


--
-- TOC entry 5171 (class 0 OID 24768)
-- Dependencies: 236
-- Data for Name: payments; Type: TABLE DATA; Schema: public; Owner: postgres
--

COPY public.payments (id, order_id, method, amount, paid_at, status) FROM stdin;
1	1	cash	1180.00	2026-07-22 22:59:04.673+07	paid
2	2	transfer	120.00	2026-07-22 23:14:12.502188+07	paid
\.


--
-- TOC entry 5169 (class 0 OID 24745)
-- Dependencies: 234
-- Data for Name: service_order_items; Type: TABLE DATA; Schema: public; Owner: postgres
--

COPY public.service_order_items (id, order_id, service_id, service_code, service_name, price) FROM stdin;
1	1	1	wash	Basic wash	100.00
2	1	2	washVacuum	Wash and vacuum	160.00
3	1	5	fullEngine	Full wash with underbody and engine bay	460.00
4	1	7	wax	Wax coating	460.00
5	2	1	wash	Basic wash	120.00
\.


--
-- TOC entry 5167 (class 0 OID 24703)
-- Dependencies: 232
-- Data for Name: service_orders; Type: TABLE DATA; Schema: public; Owner: postgres
--

COPY public.service_orders (id, queue_no, customer_id, vehicle_id, status, payment_method, total_amount, note, created_by, started_at, completed_at, created_at, updated_at) FROM stdin;
1	Q20260722-0001	1	1	pending	cash	1180.00	\N	1	\N	\N	2026-07-22 22:59:04.673+07	2026-07-22 22:59:04.673+07
2	Q20260722-0002	2	2	pending	transfer	120.00	\N	1	\N	\N	2026-07-22 23:14:12.502188+07	2026-07-22 23:14:12.502188+07
\.


--
-- TOC entry 5165 (class 0 OID 24682)
-- Dependencies: 230
-- Data for Name: service_prices; Type: TABLE DATA; Schema: public; Owner: postgres
--

COPY public.service_prices (id, service_id, vehicle_category, size_code, price) FROM stdin;
1	1	bike	L	120.00
2	1	bike	M	100.00
3	1	bike	S	80.00
4	1	car	XL	160.00
5	1	car	L	140.00
6	1	car	M	120.00
7	1	car	S	100.00
8	2	car	XL	260.00
9	2	car	L	220.00
10	2	car	M	180.00
11	2	car	S	160.00
12	3	car	XL	460.00
13	3	car	L	420.00
14	3	car	M	380.00
15	3	car	S	360.00
16	4	car	XL	360.00
17	4	car	L	320.00
18	4	car	M	280.00
19	4	car	S	260.00
20	5	car	XL	560.00
21	5	car	L	520.00
22	5	car	M	480.00
23	5	car	S	460.00
24	6	car	XL	600.00
25	6	car	L	600.00
26	6	car	M	500.00
27	6	car	S	500.00
28	7	car	XL	860.00
29	7	car	L	620.00
30	7	car	M	480.00
31	7	car	S	460.00
\.


--
-- TOC entry 5163 (class 0 OID 24664)
-- Dependencies: 228
-- Data for Name: services; Type: TABLE DATA; Schema: public; Owner: postgres
--

COPY public.services (id, code, name, category, estimated_minutes, is_active) FROM stdin;
1	wash	Basic wash	all	25	t
2	washVacuum	Wash and vacuum	car	35	t
3	fullFlush	Wash, vacuum and underbody flush	car	50	t
4	engineWash	Wash, vacuum and engine bay wash	car	55	t
5	fullEngine	Full wash with underbody and engine bay	car	70	t
6	ozone	Ozone deodorizing	car	45	t
7	wax	Wax coating	car	60	t
\.


--
-- TOC entry 5155 (class 0 OID 24578)
-- Dependencies: 220
-- Data for Name: staff; Type: TABLE DATA; Schema: public; Owner: postgres
--

COPY public.staff (id, employee_code, full_name, "position", daily_wage, pin_hash, is_active, created_at, updated_at) FROM stdin;
1	S01	Somchai	Car wash staff	350.00	\N	t	2026-07-22 22:55:32.85073+07	2026-07-22 22:55:32.85073+07
2	S02	Somsak	Detailing staff	450.00	\N	t	2026-07-22 22:55:32.85073+07	2026-07-22 22:55:32.85073+07
3	S03	Wichai	Front desk staff	400.00	\N	t	2026-07-22 22:55:32.85073+07	2026-07-22 22:55:32.85073+07
4	S04	maetanon	Staff	359.00	\N	f	2026-07-22 23:09:01.956389+07	2026-07-22 23:09:50.967591+07
\.


--
-- TOC entry 5173 (class 0 OID 24791)
-- Dependencies: 238
-- Data for Name: staff_attendance; Type: TABLE DATA; Schema: public; Owner: postgres
--

COPY public.staff_attendance (id, staff_id, work_date, check_in_at, check_out_at, method) FROM stdin;
\.


--
-- TOC entry 5161 (class 0 OID 24642)
-- Dependencies: 226
-- Data for Name: vehicles; Type: TABLE DATA; Schema: public; Owner: postgres
--

COPY public.vehicles (id, customer_id, license_plate, province, category, size_code, created_at) FROM stdin;
1	1	dwd	\N	car	S	2026-07-22 22:59:04.673+07
2	2	191	\N	bike	L	2026-07-22 23:14:12.502188+07
\.


--
-- TOC entry 5192 (class 0 OID 0)
-- Dependencies: 221
-- Name: app_users_id_seq; Type: SEQUENCE SET; Schema: public; Owner: postgres
--

SELECT pg_catalog.setval('public.app_users_id_seq', 5, true);


--
-- TOC entry 5193 (class 0 OID 0)
-- Dependencies: 223
-- Name: customers_id_seq; Type: SEQUENCE SET; Schema: public; Owner: postgres
--

SELECT pg_catalog.setval('public.customers_id_seq', 2, true);


--
-- TOC entry 5194 (class 0 OID 0)
-- Dependencies: 239
-- Name: finance_transactions_id_seq; Type: SEQUENCE SET; Schema: public; Owner: postgres
--

SELECT pg_catalog.setval('public.finance_transactions_id_seq', 2, true);


--
-- TOC entry 5195 (class 0 OID 0)
-- Dependencies: 235
-- Name: payments_id_seq; Type: SEQUENCE SET; Schema: public; Owner: postgres
--

SELECT pg_catalog.setval('public.payments_id_seq', 2, true);


--
-- TOC entry 5196 (class 0 OID 0)
-- Dependencies: 233
-- Name: service_order_items_id_seq; Type: SEQUENCE SET; Schema: public; Owner: postgres
--

SELECT pg_catalog.setval('public.service_order_items_id_seq', 5, true);


--
-- TOC entry 5197 (class 0 OID 0)
-- Dependencies: 231
-- Name: service_orders_id_seq; Type: SEQUENCE SET; Schema: public; Owner: postgres
--

SELECT pg_catalog.setval('public.service_orders_id_seq', 2, true);


--
-- TOC entry 5198 (class 0 OID 0)
-- Dependencies: 229
-- Name: service_prices_id_seq; Type: SEQUENCE SET; Schema: public; Owner: postgres
--

SELECT pg_catalog.setval('public.service_prices_id_seq', 31, true);


--
-- TOC entry 5199 (class 0 OID 0)
-- Dependencies: 227
-- Name: services_id_seq; Type: SEQUENCE SET; Schema: public; Owner: postgres
--

SELECT pg_catalog.setval('public.services_id_seq', 7, true);


--
-- TOC entry 5200 (class 0 OID 0)
-- Dependencies: 237
-- Name: staff_attendance_id_seq; Type: SEQUENCE SET; Schema: public; Owner: postgres
--

SELECT pg_catalog.setval('public.staff_attendance_id_seq', 1, false);


--
-- TOC entry 5201 (class 0 OID 0)
-- Dependencies: 219
-- Name: staff_id_seq; Type: SEQUENCE SET; Schema: public; Owner: postgres
--

SELECT pg_catalog.setval('public.staff_id_seq', 4, true);


--
-- TOC entry 5202 (class 0 OID 0)
-- Dependencies: 225
-- Name: vehicles_id_seq; Type: SEQUENCE SET; Schema: public; Owner: postgres
--

SELECT pg_catalog.setval('public.vehicles_id_seq', 2, true);


--
-- TOC entry 4959 (class 2606 OID 24618)
-- Name: app_users app_users_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.app_users
    ADD CONSTRAINT app_users_pkey PRIMARY KEY (id);


--
-- TOC entry 4961 (class 2606 OID 24620)
-- Name: app_users app_users_username_key; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.app_users
    ADD CONSTRAINT app_users_username_key UNIQUE (username);


--
-- TOC entry 4963 (class 2606 OID 24640)
-- Name: customers customers_phone_key; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.customers
    ADD CONSTRAINT customers_phone_key UNIQUE (phone);


--
-- TOC entry 4965 (class 2606 OID 24638)
-- Name: customers customers_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.customers
    ADD CONSTRAINT customers_pkey PRIMARY KEY (id);


--
-- TOC entry 4993 (class 2606 OID 24831)
-- Name: finance_transactions finance_transactions_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.finance_transactions
    ADD CONSTRAINT finance_transactions_pkey PRIMARY KEY (id);


--
-- TOC entry 4987 (class 2606 OID 24784)
-- Name: payments payments_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.payments
    ADD CONSTRAINT payments_pkey PRIMARY KEY (id);


--
-- TOC entry 4985 (class 2606 OID 24756)
-- Name: service_order_items service_order_items_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.service_order_items
    ADD CONSTRAINT service_order_items_pkey PRIMARY KEY (id);


--
-- TOC entry 4981 (class 2606 OID 24726)
-- Name: service_orders service_orders_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.service_orders
    ADD CONSTRAINT service_orders_pkey PRIMARY KEY (id);


--
-- TOC entry 4983 (class 2606 OID 24728)
-- Name: service_orders service_orders_queue_no_key; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.service_orders
    ADD CONSTRAINT service_orders_queue_no_key UNIQUE (queue_no);


--
-- TOC entry 4975 (class 2606 OID 24694)
-- Name: service_prices service_prices_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.service_prices
    ADD CONSTRAINT service_prices_pkey PRIMARY KEY (id);


--
-- TOC entry 4977 (class 2606 OID 24696)
-- Name: service_prices service_prices_service_id_vehicle_category_size_code_key; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.service_prices
    ADD CONSTRAINT service_prices_service_id_vehicle_category_size_code_key UNIQUE (service_id, vehicle_category, size_code);


--
-- TOC entry 4971 (class 2606 OID 24680)
-- Name: services services_code_key; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.services
    ADD CONSTRAINT services_code_key UNIQUE (code);


--
-- TOC entry 4973 (class 2606 OID 24678)
-- Name: services services_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.services
    ADD CONSTRAINT services_pkey PRIMARY KEY (id);


--
-- TOC entry 4989 (class 2606 OID 24804)
-- Name: staff_attendance staff_attendance_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.staff_attendance
    ADD CONSTRAINT staff_attendance_pkey PRIMARY KEY (id);


--
-- TOC entry 4991 (class 2606 OID 24806)
-- Name: staff_attendance staff_attendance_staff_id_work_date_key; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.staff_attendance
    ADD CONSTRAINT staff_attendance_staff_id_work_date_key UNIQUE (staff_id, work_date);


--
-- TOC entry 4955 (class 2606 OID 24600)
-- Name: staff staff_employee_code_key; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.staff
    ADD CONSTRAINT staff_employee_code_key UNIQUE (employee_code);


--
-- TOC entry 4957 (class 2606 OID 24598)
-- Name: staff staff_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.staff
    ADD CONSTRAINT staff_pkey PRIMARY KEY (id);


--
-- TOC entry 4967 (class 2606 OID 24657)
-- Name: vehicles vehicles_license_plate_province_key; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.vehicles
    ADD CONSTRAINT vehicles_license_plate_province_key UNIQUE (license_plate, province);


--
-- TOC entry 4969 (class 2606 OID 24655)
-- Name: vehicles vehicles_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.vehicles
    ADD CONSTRAINT vehicles_pkey PRIMARY KEY (id);


--
-- TOC entry 4994 (class 1259 OID 24844)
-- Name: idx_finance_transactions_occurred_at; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX idx_finance_transactions_occurred_at ON public.finance_transactions USING btree (occurred_at);


--
-- TOC entry 4978 (class 1259 OID 24843)
-- Name: idx_service_orders_created_at; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX idx_service_orders_created_at ON public.service_orders USING btree (created_at);


--
-- TOC entry 4979 (class 1259 OID 24842)
-- Name: idx_service_orders_status; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX idx_service_orders_status ON public.service_orders USING btree (status);


--
-- TOC entry 4995 (class 2606 OID 24621)
-- Name: app_users app_users_staff_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.app_users
    ADD CONSTRAINT app_users_staff_id_fkey FOREIGN KEY (staff_id) REFERENCES public.staff(id) ON DELETE SET NULL;


--
-- TOC entry 5005 (class 2606 OID 24832)
-- Name: finance_transactions finance_transactions_order_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.finance_transactions
    ADD CONSTRAINT finance_transactions_order_id_fkey FOREIGN KEY (order_id) REFERENCES public.service_orders(id) ON DELETE SET NULL;


--
-- TOC entry 5006 (class 2606 OID 24837)
-- Name: finance_transactions finance_transactions_staff_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.finance_transactions
    ADD CONSTRAINT finance_transactions_staff_id_fkey FOREIGN KEY (staff_id) REFERENCES public.staff(id) ON DELETE SET NULL;


--
-- TOC entry 5003 (class 2606 OID 24785)
-- Name: payments payments_order_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.payments
    ADD CONSTRAINT payments_order_id_fkey FOREIGN KEY (order_id) REFERENCES public.service_orders(id) ON DELETE CASCADE;


--
-- TOC entry 5001 (class 2606 OID 24757)
-- Name: service_order_items service_order_items_order_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.service_order_items
    ADD CONSTRAINT service_order_items_order_id_fkey FOREIGN KEY (order_id) REFERENCES public.service_orders(id) ON DELETE CASCADE;


--
-- TOC entry 5002 (class 2606 OID 24762)
-- Name: service_order_items service_order_items_service_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.service_order_items
    ADD CONSTRAINT service_order_items_service_id_fkey FOREIGN KEY (service_id) REFERENCES public.services(id) ON DELETE SET NULL;


--
-- TOC entry 4998 (class 2606 OID 24739)
-- Name: service_orders service_orders_created_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.service_orders
    ADD CONSTRAINT service_orders_created_by_fkey FOREIGN KEY (created_by) REFERENCES public.app_users(id) ON DELETE SET NULL;


--
-- TOC entry 4999 (class 2606 OID 24729)
-- Name: service_orders service_orders_customer_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.service_orders
    ADD CONSTRAINT service_orders_customer_id_fkey FOREIGN KEY (customer_id) REFERENCES public.customers(id);


--
-- TOC entry 5000 (class 2606 OID 24734)
-- Name: service_orders service_orders_vehicle_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.service_orders
    ADD CONSTRAINT service_orders_vehicle_id_fkey FOREIGN KEY (vehicle_id) REFERENCES public.vehicles(id);


--
-- TOC entry 4997 (class 2606 OID 24697)
-- Name: service_prices service_prices_service_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.service_prices
    ADD CONSTRAINT service_prices_service_id_fkey FOREIGN KEY (service_id) REFERENCES public.services(id) ON DELETE CASCADE;


--
-- TOC entry 5004 (class 2606 OID 24807)
-- Name: staff_attendance staff_attendance_staff_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.staff_attendance
    ADD CONSTRAINT staff_attendance_staff_id_fkey FOREIGN KEY (staff_id) REFERENCES public.staff(id) ON DELETE CASCADE;


--
-- TOC entry 4996 (class 2606 OID 24658)
-- Name: vehicles vehicles_customer_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.vehicles
    ADD CONSTRAINT vehicles_customer_id_fkey FOREIGN KEY (customer_id) REFERENCES public.customers(id) ON DELETE CASCADE;


-- Completed on 2026-07-23 00:01:10

--
-- PostgreSQL database dump complete
--

\unrestrict RS5t0xnsIjT5LDNJgdcKxytpdiVdFfc6QfGEdULxWC6XsBea3my4nt7gdcRUFiP

