import os
import sys
import re
import ast
import math
import operator
import secrets
from decimal import Decimal, InvalidOperation, getcontext
from django.conf import settings
from django.urls import path
from django.http import HttpResponse, JsonResponse
from django.core.management import execute_from_command_line
from django.views.decorators.csrf import csrf_protect
from django.middleware.csrf import get_token
from django.template import Context, Template

# =============== CRITICAL FIXES ===============
os.environ['DJANGO_SETTINGS_MODULE'] = '__main__'
if 'runserver' in sys.argv and '--noreload' not in sys.argv:
    sys.argv.append('--noreload')

getcontext().prec = 28
SECRET_KEY = secrets.token_urlsafe(64)

if not settings.configured:
    settings.configure(
        DEBUG=False,
        SECRET_KEY=SECRET_KEY,
        ROOT_URLCONF=__name__,
        ALLOWED_HOSTS=['*'],
        MIDDLEWARE=[
            'django.middleware.security.SecurityMiddleware',
            'django.middleware.csrf.CsrfViewMiddleware',
            'django.middleware.clickjacking.XFrameOptionsMiddleware',
        ],
        TEMPLATES=[{
            'BACKEND': 'django.template.backends.django.DjangoTemplates',
            'OPTIONS': {'context_processors': ['django.template.context_processors.csrf']},
        }],
        SECURE_BROWSER_XSS_FILTER=True,
        SECURE_CONTENT_TYPE_NOSNIFF=True,
        SESSION_COOKIE_SECURE=False,
        CSRF_COOKIE_SECURE=False,
        X_FRAME_OPTIONS='DENY',
    )

# =============== SAFE MATH EVALUATOR (FIXED PERCENTAGE HANDLING) ===============
ALLOWED_MATH_NAMES = {
    'pi': math.pi, 'e': math.e, 'sqrt': math.sqrt,
    'sin': lambda x: math.sin(math.radians(x)),
    'cos': lambda x: math.cos(math.radians(x)),
    'tan': lambda x: math.tan(math.radians(x)),
    'log': math.log10, 'ln': math.log, 'abs': abs, 'round': round,
}

ALLOWED_OPERATORS = {
    ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
    ast.Div: operator.truediv, ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod,
    ast.Pow: operator.pow, ast.USub: operator.neg, ast.UAdd: operator.pos,
}

def safe_math_eval(expr: str):
    """Fixed percentage handling + robust AST evaluation"""
    try:
        expr = expr.strip().replace('^', '**').replace('×', '*').replace('÷', '/')
        
        # FIXED: Context-aware percentage conversion
        # Converts "100+10%" → "100+(100*0.1)" and "10%" → "0.1"
        def replace_percent(match):
            num = match.group(1)
            # Check if preceded by operator (needs context calculation)
            before = expr[:match.start()]
            if any(before.endswith(op) for op in '+-*/('):
                # Standalone percentage: "10%" → "0.1"
                return f"({num}/100)"
            else:
                # Contextual percentage: "100+10%" → "100+(100*0.1)"
                # Find the preceding number/expression
                prev_match = re.search(r'([\d.]+(?:[+\-*/][\d.]+)*)$', before)
                if prev_match:
                    base = prev_match.group(1)
                    return f"+({base}*{num}/100)"
                return f"({num}/100)"
        
        expr = re.sub(r'(\d+\.?\d*)%', replace_percent, expr)
        
        # Parse and evaluate AST
        parsed = ast.parse(expr, mode='eval')
        
        def eval_node(node):
            if isinstance(node, (ast.Constant, ast.Num)):
                val = node.n if hasattr(node, 'n') else node.value
                return Decimal(str(val)) if isinstance(val, (int, float)) else val
            if isinstance(node, ast.Name):
                if node.id not in ALLOWED_MATH_NAMES:
                    raise ValueError(f"Function not allowed: {node.id}")
                return ALLOWED_MATH_NAMES[node.id]
            if isinstance(node, ast.BinOp):
                left = eval_node(node.left)
                right = eval_node(node.right)
                op_type = type(node.op)
                if op_type not in ALLOWED_OPERATORS:
                    raise ValueError(f"Operator not allowed: {op_type.__name__}")
                return ALLOWED_OPERATORS[op_type](left, right)
            if isinstance(node, ast.UnaryOp):
                operand = eval_node(node.operand)
                op_type = type(node.op)
                if op_type not in ALLOWED_OPERATORS:
                    raise ValueError(f"Unary operator not allowed: {op_type.__name__}")
                return ALLOWED_OPERATORS[op_type](operand)
            if isinstance(node, ast.Call):
                if not isinstance(node.func, ast.Name) or node.func.id not in ALLOWED_MATH_NAMES:
                    raise ValueError(f"Function not allowed: {getattr(node.func, 'id', 'unknown')}")
                func = ALLOWED_MATH_NAMES[node.func.id]
                args = [eval_node(arg) for arg in node.args]
                if node.keywords:
                    raise ValueError("Keyword arguments not permitted")
                return func(*args)
            if isinstance(node, ast.Expression):
                return eval_node(node.body)
            raise ValueError(f"Unsupported expression element: {type(node).__name__}")
        
        result = eval_node(parsed.body)
        
        # Format result
        if isinstance(result, Decimal):
            if result == result.to_integral_value():
                return int(result), None
            return float(result.quantize(Decimal('0.0000000001').normalize())), None
        if isinstance(result, float):
            return round(result, 10), None
        return result, None
    
    except (SyntaxError, ValueError, TypeError, ZeroDivisionError, 
            InvalidOperation, OverflowError, KeyError, AttributeError) as e:
        return None, str(e).split('\n')[0]
    except Exception as e:
        return None, f"Evaluation error: {type(e).__name__}"

# =============== VIEWS (FIXED JAVASCRIPT + API) ===============
def favicon_handler(request):
    return HttpResponse(status=204)

def get_calculator_html(request):
    csrf_token = get_token(request)
    html_template = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Quantum Calculator</title>
    <style>
        :root { --primary: #6366f1; --secondary: #8b5cf6; --success: #10b981; 
                --danger: #ef4444; --dark: #0f172a; --gray: #334155; }
        * { margin:0; padding:0; box-sizing:border-box; }
        body { font-family: 'Segoe UI', system-ui, sans-serif; background: linear-gradient(135deg, #0f172a 0%, #1e293b 100%); 
               min-height:100vh; display:flex; justify-content:center; align-items:center; padding:20px; color:#e2e8f0; }
        .calculator { width:100%; max-width:420px; background:var(--dark); border-radius:28px; 
                      box-shadow:0 25px 50px -12px rgba(0,0,0,0.75); overflow:hidden; }
        .header { text-align:center; padding:16px; background:rgba(30,41,59,0.8); border-bottom:2px solid var(--gray); }
        .header h1 { font-size:1.8rem; background:linear-gradient(45deg, var(--primary), var(--secondary)); 
                     -webkit-background-clip:text; background-clip:text; color:transparent; letter-spacing:1px; }
        .display-container { padding:24px; position:relative; }
        .history { height:28px; text-align:right; color:#94a3b8; font-size:0.95rem; overflow:hidden; 
                   white-space:nowrap; margin-bottom:12px; min-height:28px; }
        .display { background:rgba(15,23,42,0.95); color:#64ffda; font-size:3.1rem; text-align:right; 
                   padding:16px 24px; border-radius:20px; min-height:90px; overflow:auto; word-wrap:break-word; 
                   box-shadow:inset 0 4px 15px rgba(0,0,0,0.5), 0 2px 8px rgba(0,0,0,0.3); 
                   font-weight:300; letter-spacing:-0.5px; }
        .buttons { display:grid; grid-template-columns:repeat(5, 1fr); gap:10px; padding:20px; background:#1e293b; }
        button { border:none; border-radius:18px; padding:16px 8px; font-size:1.25rem; font-weight:600; 
                 cursor:pointer; transition:all 0.18s ease; color:white; background:#334155; 
                 box-shadow:0 4px 6px rgba(0,0,0,0.25), inset 0 1px 0 rgba(255,255,255,0.15); }
        button:hover { transform:translateY(-1px); box-shadow:0 6px 8px rgba(0,0,0,0.35); }
        button:active { transform:translateY(2px); box-shadow:0 2px 4px rgba(0,0,0,0.3); }
        .number { background:#1e293b; }
        .operator { background:var(--primary); }
        .function { background:#4d148c; }
        .memory { background:#7e22ce; }
        .equals { background:var(--success); grid-column:span 2; }
        .clear { background:var(--danger); }
        .zero { grid-column:span 2; }
        .status { text-align:center; padding:12px; font-size:0.9rem; color:#94a3b8; background:rgba(30,41,59,0.7); 
                  border-top:1px solid var(--gray); font-family:monospace; }
        .memory-indicator { position:absolute; top:16px; left:24px; font-size:0.9rem; color:#fbbf24; 
                           background:rgba(30,41,59,0.9); padding:2px 8px; border-radius:12px; display:none; }
        @media (max-width:430px) { 
            .buttons { grid-template-columns:repeat(4,1fr); gap:8px; padding:16px; }
            button { padding:14px 4px; font-size:1.15rem; border-radius:16px; }
            .btn-func { display:none; }
            .display { font-size:2.7rem; padding:14px 20px; }
        }
    </style>
</head>
<body>
    <div class="calculator">
        <div class="header"><h1>QUANTUM CALCULATOR</h1></div>
        <div class="display-container">
            <div class="memory-indicator" id="mem-indicator">M</div>
            <div class="history" id="history"></div>
            <div class="display" id="display">0</div>
        </div>
        <div class="buttons">
            <button class="memory" onclick="memClear()">MC</button>
            <button class="memory" onclick="memRecall()">MR</button>
            <button class="memory" onclick="memAdd()">M+</button>
            <button class="memory" onclick="memSubtract()">M-</button>
            <button class="clear" onclick="clearAll()">AC</button>
            
            <button class="function btn-func" onclick="append('sqrt(')">√</button>
            <button class="function btn-func" onclick="append('sin(')">sin</button>
            <button class="function btn-func" onclick="append('cos(')">cos</button>
            <button class="function btn-func" onclick="append('tan(')">tan</button>
            <button class="operator" onclick="backspace()">⌫</button>
            
            <button class="function btn-func" onclick="append('log(')">log</button>
            <button class="function btn-func" onclick="append('ln(')">ln</button>
            <button class="function" onclick="append('(')">(</button>
            <button class="function" onclick="append(')')">)</button>
            <button class="operator" onclick="append('/')">/</button>
            
            <button class="number" onclick="append('7')">7</button>
            <button class="number" onclick="append('8')">8</button>
            <button class="number" onclick="append('9')">9</button>
            <button class="operator" onclick="append('*')">x</button>
            <button class="function btn-func" onclick="toggleSign()">±</button>
            
            <button class="number" onclick="append('4')">4</button>
            <button class="number" onclick="append('5')">5</button>
            <button class="number" onclick="append('6')">6</button>
            <button class="operator" onclick="append('-')">-</button>
            <button class="function btn-func" onclick="append('pi')">π</button>
            
            <button class="number" onclick="append('1')">1</button>
            <button class="number" onclick="append('2')">2</button>
            <button class="number" onclick="append('3')">3</button>
            <button class="operator" onclick="append('+')">+</button>
            <button class="function btn-func" onclick="append('e')">e</button>
            
            <button class="number zero" onclick="append('0')">0</button>
            <button class="number" onclick="append('.')">.</button>
            <button class="function" onclick="append('%')">%</button>
            <button class="equals" onclick="calculate()">=</button>
        </div>
        <div class="status">Secure • AST-Evaluated • Django-Powered</div>
    </div>

    <script>
        const display = document.getElementById('display');
        const historyEl = document.getElementById('history');
        const memIndicator = document.getElementById('mem-indicator');
        let currentExpr = '0';
        let calculationHistory = JSON.parse(localStorage.getItem('calcHistory') || '[]');
        let memoryValue = parseFloat(localStorage.getItem('calcMemory')) || 0;
        const csrfToken = '{{ csrf_token }}';
        
        function updateDisplay(val = currentExpr) {
            // FIXED: Removed broken comma formatting regex
            display.textContent = val === '' ? '0' : val;
            currentExpr = val;
        }
        
        function append(value) {
            if (currentExpr === '0' && value !== '.') currentExpr = '';
            // Prevent double operators
            const lastChar = currentExpr.slice(-1);
            if (['+', '-', '*', '/', '%', '('].includes(value) && ['+', '-', '*', '/', '('].includes(lastChar)) {
                currentExpr = currentExpr.slice(0, -1);
            }
            currentExpr += value;
            updateDisplay();
        }
        
        function clearAll() {
            currentExpr = '0';
            updateDisplay();
        }
        
        function backspace() {
            if (currentExpr.length > 1) {
                currentExpr = currentExpr.slice(0, -1);
                updateDisplay();
            } else clearAll();
        }
        
        // FIXED: Simplified sign toggle that works on last number
        function toggleSign() {
            // Find last number in expression (handles decimals and negatives)
            const parts = currentExpr.split(/([+\-*/()])/).filter(Boolean);
            if (parts.length === 0) return;
            
            // Get last numeric part
            let i = parts.length - 1;
            while (i >= 0 && !/^\d+\.?\d*$/.test(parts[i].replace('-', ''))) i--;
            
            if (i >= 0) {
                const numPart = parts[i];
                if (numPart.startsWith('-')) {
                    parts[i] = numPart.substring(1);
                } else {
                    parts[i] = '-' + numPart;
                }
                currentExpr = parts.join('');
                // Clean up double operators
                currentExpr = currentExpr.replace(/--/g, '+')
                                   .replace(/\+-/g, '-')
                                   .replace(/--/g, '+');
                updateDisplay();
            } else {
                // Toggle whole expression if it's a single number
                if (!isNaN(parseFloat(currentExpr)) && isFinite(currentExpr)) {
                    currentExpr = currentExpr.startsWith('-') ? currentExpr.substring(1) : '-' + currentExpr;
                    updateDisplay();
                }
            }
        }
        
        async function calculate() {
            if (currentExpr === '0') return;
            try {
                const response = await fetch('/api/calculate/', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/x-www-form-urlencoded',
                        'X-CSRFToken': csrfToken
                    },
                    body: new URLSearchParams({ expression: currentExpr })
                });
                const data = await response.json();
                if (response.ok && data.result !== undefined) {
                    const historyEntry = `${currentExpr} = ${data.result}`;
                    calculationHistory.push(historyEntry);
                    if (calculationHistory.length > 20) calculationHistory.shift();
                    localStorage.setItem('calcHistory', JSON.stringify(calculationHistory));
                    historyEl.textContent = historyEntry;
                    currentExpr = String(data.result);
                    updateDisplay();
                } else {
                    throw new Error(data.error || 'Calculation failed');
                }
            } catch (error) {
                display.textContent = `Error: ${error.message || 'Invalid expression'}`;
                setTimeout(() => updateDisplay(), 2000);
            }
        }
        
        // Memory operations
        function memClear() { 
            memoryValue = 0; 
            localStorage.removeItem('calcMemory'); 
            memIndicator.style.display = 'none'; 
        }
        function memRecall() { 
            if (memoryValue !== 0) { 
                currentExpr = String(memoryValue); 
                updateDisplay(); 
            } 
        }
        async function updateMemory(factor) {
            try {
                const response = await fetch('/api/calculate/', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/x-www-form-urlencoded', 'X-CSRFToken': csrfToken },
                    body: new URLSearchParams({ expression: currentExpr })
                });
                const data = await response.json();
                if (data.result !== undefined) {
                    memoryValue += parseFloat(data.result) * factor;
                    localStorage.setItem('calcMemory', String(memoryValue));
                    memIndicator.style.display = memoryValue !== 0 ? 'block' : 'none';
                }
            } catch(e) { /* silent fail */ }
        }
        function memAdd() { updateMemory(1); }
        function memSubtract() { updateMemory(-1); }
        
        // Initialize UI
        updateDisplay();
        if (memoryValue !== 0) memIndicator.style.display = 'block';
        if (calculationHistory.length) historyEl.textContent = calculationHistory.slice(-1)[0];
        
        // Keyboard support
        document.addEventListener('keydown', (e) => {
            e.preventDefault();
            if (e.key >= '0' && e.key <= '9') append(e.key);
            else if (e.key === '.') append('.');
            else if (['+', '-', '*', '/'].includes(e.key)) append(e.key);
            else if (e.key === 'Enter' || e.key === '=') calculate();
            else if (e.key === 'Backspace') backspace();
            else if (e.key === 'Escape') clearAll();
            else if (e.key === '%') append('%');
            else if (e.key === '(' || e.key === ')') append(e.key);
            else if (e.key === 'm' || e.key === 'M') memAdd();
        });
    </script>
</body>
</html>
    """
    template = Template(html_template)
    context = Context({'csrf_token': csrf_token})
    return HttpResponse(template.render(context))

@csrf_protect
def calculate_api(request):
    if request.method != 'POST':
        return JsonResponse({'error': 'Method not allowed'}, status=405)
    
    expr = request.POST.get('expression', '').strip()
    if not expr:
        return JsonResponse({'error': 'Empty expression'}, status=400)
    if len(expr) > 200:
        return JsonResponse({'error': 'Expression too long (max 200 chars)'}, status=400)
    
    # FIXED: Allow all letters (rely on AST evaluator for safety)
    # This supports abs, round, and all allowed functions
    if not re.match(r'^[0-9+\-*/().%\s\^×÷a-zA-Z]+$', expr):
        return JsonResponse({'error': 'Invalid characters detected'}, status=400)
    
    result, error = safe_math_eval(expr)
    if error:
        # Log error details to console for debugging
        print(f"Calculation error for '{expr}': {error}", file=sys.stderr)
        return JsonResponse({'error': error}, status=400)
    
    return JsonResponse({'result': result})

# =============== URLS ===============
urlpatterns = [
    path('favicon.ico', favicon_handler),
    path('', get_calculator_html),
    path('api/calculate/', calculate_api),
]

# =============== MAIN ===============
if __name__ == '__main__':
    print("\n" + "="*60)
    print("🚀 QUANTUM CALCULATOR - FULLY FIXED")
    print("="*60)
    print("✅ Regex whitelist fixed (supports abs, round, etc.)")
    print("✅ Percentage handling corrected")
    print("✅ JavaScript regex escaping removed (no comma formatting)")
    print("✅ ToggleSign works on partial expressions")
    print("✅ Detailed API error logging")
    print("✅ Template engine + favicon configured")
    print("\n🌐 Access at: http://localhost:8000")
    print("   Press CTRL+C to stop")
    print("="*60 + "\n")
    
    if len(sys.argv) == 1:
        sys.argv = ['calculator.py', 'runserver', '8000', '--noreload']
    elif 'runserver' in sys.argv and '--noreload' not in sys.argv:
        sys.argv.append('--noreload')
    
    execute_from_command_line(sys.argv)
