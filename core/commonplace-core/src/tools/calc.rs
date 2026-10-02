//! Arithmetic evaluator for model-written expressions: + - * / and parentheses over decimals.

use anyhow::{Result, bail};

pub fn eval(expr: &str) -> Result<f64> {
    let toks: Vec<char> = expr.chars().filter(|c| !c.is_whitespace()).collect();
    let mut p = Parser { t: &toks, i: 0 };
    let v = p.sum()?;
    if p.i != toks.len() {
        bail!("unexpected '{}' in expression", toks[p.i]);
    }
    if !v.is_finite() {
        bail!("result is not finite");
    }
    Ok(v)
}

/// Numeric literals in an expression, normalized like `text::numbers`.
pub fn literals(expr: &str) -> Vec<String> {
    let mut out = Vec::new();
    let mut cur = String::new();
    for c in expr.chars().chain(std::iter::once(' ')) {
        if c.is_ascii_digit() || c == '.' {
            cur.push(c);
        } else if !cur.is_empty() {
            out.push(std::mem::take(&mut cur));
        }
    }
    out
}

struct Parser<'a> {
    t: &'a [char],
    i: usize,
}

impl Parser<'_> {
    fn peek(&self) -> Option<char> {
        self.t.get(self.i).copied()
    }

    fn sum(&mut self) -> Result<f64> {
        let mut v = self.product()?;
        while let Some(c @ ('+' | '-')) = self.peek() {
            self.i += 1;
            let r = self.product()?;
            v = if c == '+' { v + r } else { v - r };
        }
        Ok(v)
    }

    fn product(&mut self) -> Result<f64> {
        let mut v = self.unary()?;
        while let Some(c @ ('*' | '/')) = self.peek() {
            self.i += 1;
            let r = self.unary()?;
            if c == '/' && r == 0.0 {
                bail!("division by zero");
            }
            v = if c == '*' { v * r } else { v / r };
        }
        Ok(v)
    }

    fn unary(&mut self) -> Result<f64> {
        if self.peek() == Some('-') {
            self.i += 1;
            return Ok(-self.unary()?);
        }
        self.atom()
    }

    fn atom(&mut self) -> Result<f64> {
        match self.peek() {
            Some('(') => {
                self.i += 1;
                let v = self.sum()?;
                if self.peek() != Some(')') {
                    bail!("missing ')'");
                }
                self.i += 1;
                Ok(v)
            }
            Some(c) if c.is_ascii_digit() || c == '.' => {
                let s = self.i;
                while self.peek().is_some_and(|c| c.is_ascii_digit() || c == '.') {
                    self.i += 1;
                }
                let lit: String = self.t[s..self.i].iter().collect();
                Ok(lit.parse()?)
            }
            Some(c) => bail!("unexpected '{c}'"),
            None => bail!("unexpected end of expression"),
        }
    }
}
