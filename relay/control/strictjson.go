package main

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"io"
)

// checkNoDuplicates walks a JSON document and refuses an object with the same member twice, at any depth.
// encoding/json keeps the last of two equal members silently; a reader that keeps the first would see another value.
func checkNoDuplicates(data []byte) error {
	dec := json.NewDecoder(bytes.NewReader(data))
	dec.UseNumber()
	if err := walkValue(dec, 0); err != nil {
		return err
	}
	if _, err := dec.Token(); err != io.EOF {
		return errors.New("trailing data after the JSON value")
	}
	return nil
}

func walkValue(dec *json.Decoder, depth int) error {
	if depth > 16 {
		return errors.New("JSON nested too deep")
	}
	tok, err := dec.Token()
	if err != nil {
		return err
	}
	switch t := tok.(type) {
	case json.Delim:
		switch t {
		case '{':
			seen := map[string]bool{}
			for dec.More() {
				k, err := dec.Token()
				if err != nil {
					return err
				}
				ks, ok := k.(string)
				if !ok {
					return errors.New("object key is not a string")
				}
				if seen[ks] {
					return fmt.Errorf("member %q appears twice", ks)
				}
				seen[ks] = true
				if err := walkValue(dec, depth+1); err != nil {
					return err
				}
			}
			_, err := dec.Token() // '}'
			return err
		case '[':
			for dec.More() {
				if err := walkValue(dec, depth+1); err != nil {
					return err
				}
			}
			_, err := dec.Token() // ']'
			return err
		}
		return errors.New("unexpected delimiter")
	default:
		return nil
	}
}

// decodeStrict decodes one JSON object into v: no unknown members, no duplicate members, nothing after it.
func decodeStrict(data []byte, v any) error {
	if len(bytes.TrimSpace(data)) == 0 {
		data = []byte("{}")
	}
	if !fault("allow-unknown") {
		if err := checkNoDuplicates(data); err != nil {
			return err
		}
	}
	dec := json.NewDecoder(bytes.NewReader(data))
	if !fault("allow-unknown") {
		dec.DisallowUnknownFields()
	}
	if err := dec.Decode(v); err != nil {
		return err
	}
	if dec.More() {
		return errors.New("trailing data after the JSON value")
	}
	return nil
}
