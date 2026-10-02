//! Deterministic unit conversion from query patterns like "10 miles in km".

use regex::Regex;
use std::sync::LazyLock;

#[derive(Clone, Copy, PartialEq)]
enum Dim {
    Length,
    Mass,
    Volume,
    Area,
    Speed,
    Temp,
}

/// (names, dimension, factor to the SI base unit). Temperatures are handled separately.
const UNITS: &[(&[&str], Dim, f64, &str)] = &[
    (&["mm", "millimeter", "millimeters", "millimetre", "millimetres"], Dim::Length, 0.001, "mm"),
    (&["cm", "centimeter", "centimeters", "centimetre", "centimetres"], Dim::Length, 0.01, "cm"),
    (&["m", "meter", "meters", "metre", "metres"], Dim::Length, 1.0, "m"),
    (&["km", "kilometer", "kilometers", "kilometre", "kilometres"], Dim::Length, 1000.0, "km"),
    (&["in", "inch", "inches"], Dim::Length, 0.0254, "in"),
    (&["ft", "foot", "feet"], Dim::Length, 0.3048, "ft"),
    (&["yd", "yard", "yards"], Dim::Length, 0.9144, "yd"),
    (&["mi", "mile", "miles"], Dim::Length, 1609.344, "mi"),
    (&["nmi", "nautical mile", "nautical miles"], Dim::Length, 1852.0, "nmi"),
    (&["mg", "milligram", "milligrams"], Dim::Mass, 1e-6, "mg"),
    (&["g", "gram", "grams"], Dim::Mass, 0.001, "g"),
    (&["kg", "kilogram", "kilograms", "kilo", "kilos"], Dim::Mass, 1.0, "kg"),
    (&["t", "tonne", "tonnes", "metric ton", "metric tons"], Dim::Mass, 1000.0, "t"),
    (&["oz", "ounce", "ounces"], Dim::Mass, 0.028349523125, "oz"),
    (&["lb", "lbs", "pound", "pounds"], Dim::Mass, 0.45359237, "lb"),
    (&["st", "stone", "stones"], Dim::Mass, 6.35029318, "st"),
    (&["ml", "milliliter", "milliliters", "millilitre", "millilitres"], Dim::Volume, 0.001, "ml"),
    (&["l", "liter", "liters", "litre", "litres"], Dim::Volume, 1.0, "L"),
    (&["gal", "gallon", "gallons", "us gallon", "us gallons"], Dim::Volume, 3.785411784, "US gal"),
    (&["imperial gallon", "imperial gallons"], Dim::Volume, 4.54609, "imp gal"),
    (&["cup", "cups"], Dim::Volume, 0.2365882365, "cups"),
    (&["fl oz", "fluid ounce", "fluid ounces"], Dim::Volume, 0.0295735295625, "fl oz"),
    (&["tbsp", "tablespoon", "tablespoons"], Dim::Volume, 0.01478676478125, "tbsp"),
    (&["tsp", "teaspoon", "teaspoons"], Dim::Volume, 0.00492892159375, "tsp"),
    (&["m2", "square meter", "square meters", "square metre", "square metres", "sq m"], Dim::Area, 1.0, "m²"),
    (&["km2", "square kilometer", "square kilometers", "square kilometre", "square kilometres", "sq km"], Dim::Area, 1e6, "km²"),
    (&["ha", "hectare", "hectares"], Dim::Area, 1e4, "ha"),
    (&["acre", "acres"], Dim::Area, 4046.8564224, "acres"),
    (&["sq mi", "square mile", "square miles", "mi2"], Dim::Area, 2_589_988.110336, "sq mi"),
    (&["sq ft", "square foot", "square feet", "ft2"], Dim::Area, 0.09290304, "sq ft"),
    (&["km/h", "kph", "kmh", "kilometers per hour", "kilometres per hour"], Dim::Speed, 1.0 / 3.6, "km/h"),
    (&["mph", "miles per hour"], Dim::Speed, 0.44704, "mph"),
    (&["m/s", "meters per second", "metres per second"], Dim::Speed, 1.0, "m/s"),
    (&["knot", "knots", "kn"], Dim::Speed, 0.514444, "kn"),
    (&["c", "°c", "celsius", "degrees celsius", "centigrade"], Dim::Temp, 0.0, "°C"),
    (&["f", "°f", "fahrenheit", "degrees fahrenheit"], Dim::Temp, 0.0, "°F"),
    (&["k", "kelvin", "kelvins"], Dim::Temp, 0.0, "K"),
];

static PATTERN: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"(?i)(-?\d[\d,]*(?:\.\d+)?)\s*([a-z°/ 2]+?)\s+(?:in|to|into|as)\s+([a-z°/ 2]+?)\s*\??$").unwrap()
});

fn find(name: &str) -> Option<&'static (&'static [&'static str], Dim, f64, &'static str)> {
    let n = name.trim().to_lowercase();
    let n = n.strip_prefix("degrees ").map(|s| s.to_string()).unwrap_or(n);
    UNITS.iter().find(|u| u.0.contains(&n.as_str()))
}

fn to_kelvin(v: f64, unit: &str) -> f64 {
    match unit {
        "°C" => v + 273.15,
        "°F" => (v - 32.0) * 5.0 / 9.0 + 273.15,
        _ => v,
    }
}

fn from_kelvin(k: f64, unit: &str) -> f64 {
    match unit {
        "°C" => k - 273.15,
        "°F" => (k - 273.15) * 9.0 / 5.0 + 32.0,
        _ => k,
    }
}

fn round_sig(x: f64) -> String {
    if x == 0.0 {
        return "0".into();
    }
    let digits = (4 - x.abs().log10().floor() as i32 - 1).max(0) as usize;
    let s = format!("{x:.digits$}");
    if s.contains('.') { s.trim_end_matches('0').trim_end_matches('.').to_string() } else { s }
}

/// If the query asks for a unit conversion, return a display line such as `10 mi = 16.09 km`.
pub fn convert_query(query: &str) -> Option<String> {
    let q = query.trim().trim_start_matches(|c: char| !c.is_ascii_digit() && c != '-');
    let q = q.trim_end_matches(['?', '.', '!']);
    let caps = PATTERN.captures(q)?;
    let v: f64 = caps[1].replace(',', "").parse().ok()?;
    let (from, to) = (find(&caps[2])?, find(&caps[3])?);
    if from.1 != to.1 {
        return None;
    }
    let out = if from.1 == Dim::Temp { from_kelvin(to_kelvin(v, from.3), to.3) } else { v * from.2 / to.2 };
    Some(format!("{} {} = {} {}", round_sig(v), from.3, round_sig(out), to.3))
}
